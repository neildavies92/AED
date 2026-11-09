"""Linux host admission and independent watchdog for the one-shot publisher."""

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

ROOT = Path("/srv/aed")
LONDON = ZoneInfo("Europe/London")
DATA = "/var/lib/aed/data"
OUTCOMES = {
    "admitted",
    "published",
    "preflight_failed",
    "outcome_unverified",
    "infrastructure_unavailable",
    "disabled",
    "outside_window",
    "queue_empty",
    "already_attempted_today",
    "already_published_today",
}
REPORTS = {
    "completed",
    "not_due",
    "scheduler_never_triggered",
    "preflight_failed",
    "publishing_failed",
    "outcome_uncertain",
    "notification_failed",
    "infrastructure_unavailable",
    "publication_missing",
}


def policy():
    value = tomllib.loads((ROOT / "config/schedule.toml").read_text())
    if (
        value.get("timezone") != "Europe/London"
        or type(value.get("hour")) is not int
        or type(value.get("minute")) is not int
        or not 0 <= value["hour"] <= 23
        or not 0 <= value["minute"] <= 59
        or value["hour"] * 60 + value["minute"] > 1410
        or value.get("window_minutes") != 30
        or value.get("authorization_days") != 30
        or set(value)
        - {
            "hour",
            "minute",
            "timezone",
            "window_minutes",
            "authorization_days",
            "effective_from",
        }
    ):
        raise ValueError("hosted_policy_invalid")
    if (
        "effective_from" in value
        and date.fromisoformat(value["effective_from"]).isoformat()
        != value["effective_from"]
    ):
        raise ValueError("hosted_policy_invalid")
    return value


def slot(day, schedule):
    return datetime(
        day.year,
        day.month,
        day.day,
        schedule["hour"],
        schedule["minute"],
        tzinfo=LONDON,
    )


def eligible(now, schedule):
    local = now.astimezone(LONDON)
    return (
        not local.fold
        and (local.hour, local.minute) == (schedule["hour"], schedule["minute"])
        and local.date().isoformat() >= schedule.get("effective_from", "")
    )


def watch_day(now, schedule):
    local = now.astimezone(LONDON)
    for day in (local.date(), local.date() - timedelta(days=1)):
        deadline = (slot(day, schedule) + timedelta(minutes=35)).astimezone(UTC)
        if deadline <= now < deadline + timedelta(minutes=1):
            return day
    return None


def record(name, value):
    path = ROOT / "host" / name
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read(name):
    path = ROOT / "host" / name
    if not path.exists():
        return {}
    with path.open() as stream:
        return json.loads(stream.read(8192))


def event(name, **fields):
    print(
        json.dumps(
            {
                "event": "hosted_" + name,
                "timestamp": datetime.now(UTC).isoformat(),
                **fields,
            }
        ),
        flush=True,
    )


def compose():
    return [
        "/usr/bin/docker",
        "--host",
        "unix:///var/run/docker.sock",
        "compose",
        "--env-file",
        "/dev/null",
        "-f",
        str(ROOT / "compose.yaml"),
        *(
            ["-f", str(ROOT / "fonts.compose.yaml")]
            if (ROOT / "fonts.compose.yaml").exists()
            else []
        ),
    ]


def binding():
    db = (ROOT / "state/aed.sqlite3").stat()
    files = ["compose.yaml", "image-id", "config/aed.toml", "config/schedule.toml"]
    for optional in ("config/instagram-auth.toml", "fonts.compose.yaml"):
        if (ROOT / optional).is_file():
            files.append(optional)
    parts = [str(ROOT.resolve()), str(db.st_dev), str(db.st_ino)]
    parts.extend(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files
    )
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def verify_image():
    expected = (ROOT / "image-id").read_text().strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
        raise ValueError("image_invalid")
    model = json.loads(
        subprocess.run(
            [*compose(), "config", "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    )
    services = model["services"]
    service = services["publisher"]
    if (
        set(services) != {"publisher"}
        or service.get("image") != expected
        or service.get("pull_policy") != "never"
        or service.get("user") != "10001:10001"
        or service.get("read_only") is not True
        or service.get("privileged")
        or service.get("entrypoint")
        or service.get("ports")
        or service.get("environment")
        or service.get("network_mode")
        or service.get("restart", "no") != "no"
    ):
        raise ValueError("runtime_mismatch")
    mounts = {
        "/etc/aed": ("config", True),
        "/var/lib/aed": ("state", False),
        "/var/lib/aed-credentials": ("credentials", False),
    }
    if (ROOT / "fonts.compose.yaml").exists():
        mounts["/usr/local/share/fonts/aed"] = ("fonts", True)
    for mount in service.get("volumes", []):
        folder, readonly = mounts.pop(mount["target"])
        if (
            mount.get("type") != "bind"
            or Path(mount["source"]) != ROOT / folder
            or bool(mount.get("read_only")) != readonly
            or mount.get("bind", {}).get("create_host_path", True)
        ):
            raise ValueError("mount_mismatch")
    app = tomllib.loads((ROOT / "config/aed.toml").read_text())
    if (
        mounts
        or app.get("database_path") != "/var/lib/aed/aed.sqlite3"
        or app.get("assets_path") != "/var/lib/aed/assets"
        or app.get("account_handle") != "@ask.every.day"
    ):
        raise ValueError("authoritative_paths_invalid")


def run(schedule, *, clock=lambda: datetime.now(UTC)):
    now = clock()
    if not eligible(now, schedule):
        return 0
    day = now.astimezone(LONDON).date().isoformat()
    deadline = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    with (ROOT / "host/publish.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if read("last-invocation.json").get("local_day", "") >= day:
            return 0
        authority = read("authority.json")
        if (
            not authority
            or authority.get("binding") != binding()
            or day < authority["first_day"]
        ):
            if authority and day < authority["first_day"]:
                return 0
            record(
                "last-invocation.json",
                {"local_day": day, "outcome": "preflight_failed"},
            )
            event("authority_required", local_day=day)
            return 1
        receipt = {"local_day": day, "outcome": "admitted"}
        record("last-invocation.json", receipt)
        event("admitted", local_day=day)
        outcome = "infrastructure_unavailable"
        try:
            verify_image()
            current = clock()
            if (
                not now <= current < deadline
                or not eligible(current, schedule)
                or policy() != schedule
                or binding() != authority["binding"]
            ):
                outcome = "preflight_failed"
            else:
                outcome = "outcome_unverified"
                # Leave an admitted receipt if the host dies during a media request.
                # No timeout, restart or automatic retry of the publisher.
                result = subprocess.run(
                    [
                        *compose(),
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--name",
                        "aed-daily",
                        "publisher",
                        "schedule-run",
                        "--data-dir",
                        DATA,
                        "--log-file",
                        "/var/lib/aed/scheduler.jsonl",
                    ],
                    capture_output=True,
                    text=True,
                )
                summary = json.loads(result.stdout)
                if (
                    isinstance(summary, dict)
                    and summary.get("schema_version") == 1
                    and summary.get("local_day") == day
                ):
                    reported = summary.get("outcome")
                    if (
                        result.returncode == 0
                        and isinstance(reported, str)
                        and reported
                        in OUTCOMES
                        - {
                            "admitted",
                            "outcome_unverified",
                            "infrastructure_unavailable",
                            "preflight_failed",
                        }
                    ):
                        outcome = reported
                    elif summary.get("publication_state") == "no_attempt" or (
                        summary.get("publication_id") is None
                        and summary.get("reason")
                        in (
                            "authorization_inactive",
                            "policy_reapproval_required",
                            "approved_queue_changed",
                            "approved_snapshot_drift",
                            "schedule_configuration",
                        )
                    ):
                        outcome = "preflight_failed"
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            pass
        record("last-invocation.json", {**receipt, "outcome": outcome})
        event(outcome, local_day=day)
        return 0 if outcome in {"published", "already_published_today"} else 1


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def monitor_url():
    path = ROOT / "host/healthchecks-url"
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_uid != 0
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 128
    ):
        raise ValueError("monitor_secret_permissions")
    url = path.read_text().strip()
    if not re.fullmatch(
        r"https://hc-ping\.com/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", url
    ):
        raise ValueError("monitor_url_invalid")
    return url


def ping(report, alert_delivered):
    url = monitor_url()
    endpoint = (
        url if report in {"completed", "not_due"} or alert_delivered else url + "/fail"
    )
    request = Request(endpoint, data=report.encode("ascii"), method="POST")
    with build_opener(ProxyHandler({}), NoRedirect()).open(
        request, timeout=5
    ) as response:
        if not 200 <= response.status < 300:
            raise ValueError("monitor_ping_failed")


def monitor(schedule, now):
    day = watch_day(now, schedule)
    if day is None:
        return 0
    report = "infrastructure_unavailable"
    alert_delivered = False
    try:
        receipt = read("last-invocation.json")
        outcome = (
            receipt.get("outcome")
            if receipt.get("local_day") == day.isoformat()
            else None
        )
        verify_image()
        result = subprocess.run(
            [
                *compose(),
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "python",
                "publisher",
                "-m",
                "ask_every_day.hosted_watchdog",
                day.isoformat(),
                outcome if outcome in OUTCOMES else "missing",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=35,
        )
        verdict = json.loads(result.stdout)
        if (
            isinstance(verdict, dict)
            and isinstance(verdict.get("classification"), str)
            and verdict["classification"] in REPORTS
        ):
            report = verdict["classification"]
            alert_delivered = verdict.get("alert_delivered") is True
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        pass
    event(
        "watchdog",
        local_day=day.isoformat(),
        classification=report,
        alert_delivered=alert_delivered,
    )
    ping(report, alert_delivered)
    return 0 if report in {"completed", "not_due"} else 1


def main(command):
    try:
        schedule = policy()
        now = datetime.now(UTC)
        if command == "show":
            local = now.astimezone(LONDON)
            authority = read("authority.json")
            today = local.date()
            consumed = (
                read("last-invocation.json").get("local_day", "") >= today.isoformat()
            )
            start = max(
                today,
                date.fromisoformat(schedule.get("effective_from", today.isoformat())),
                date.fromisoformat(authority.get("first_day", today.isoformat())),
            )
            candidate = slot(start, schedule)
            while (
                candidate.astimezone(UTC) <= now
                or (consumed and candidate.date() == today)
                or candidate.astimezone(UTC).astimezone(LONDON) != candidate
            ):
                candidate = slot(candidate.date() + timedelta(days=1), schedule)
            event(
                "schedule",
                armed=authority.get("binding") == binding(),
                next_calendar_slot=candidate.isoformat(),
                next_watchdog=(candidate + timedelta(minutes=35)).isoformat(),
                last_invocation=read("last-invocation.json").get("local_day"),
            )
        elif command == "arm":
            monitor_url()
            verify_image()
            fingerprint = binding()
            challenge = "AUTHORIZE SOLE LINUX HOST " + fingerprint[:16]
            if (
                not sys.stdin.isatty()
                or not sys.stdout.isatty()
                or input(challenge + "\n> ") != challenge
            ):
                raise ValueError("confirmation_required")
            if binding() != fingerprint:
                raise ValueError("deployment_changed")
            local = datetime.now(UTC).astimezone(LONDON)
            first = local.date()
            if local >= slot(first, schedule):
                first += timedelta(days=1)
            record(
                "authority.json",
                {"binding": fingerprint, "first_day": first.isoformat()},
            )
            event("armed", first_day=first.isoformat())
        elif command == "run":
            return run(schedule)
        elif command == "monitor":
            return monitor(schedule, now)
        else:
            return 2
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        event("command_blocked", operation=command)
        return 1


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main(sys.argv[1] if len(sys.argv) == 2 else "invalid"))
