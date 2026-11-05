"""User-owned macOS trigger for the existing one-shot Docker scheduler.

This standalone standard-library script is copied outside Desktop/Documents for
launchd. Preparing/installing it never grants application publication permission.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import plistlib
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

LABEL = "com.ask-every-day.docker-publisher"
LEGACY = "com.ask-every-day.publisher"
CONTAINER = "aed-desktop-publisher"
LONDON = ZoneInfo("Europe/London")
POLICY = dict(
    timezone="Europe/London",
    hour=20,
    minute=0,
    window_minutes=30,
    authorization_days=30,
)
OUTCOMES = {
    "published",
    "disabled",
    "outside_window",
    "already_published_today",
    "already_attempted_today",
    "queue_empty",
    "blocked",
}


class PrivateLog(RotatingFileHandler):
    def _open(self):
        fd = os.open(
            self.baseFilename,
            os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
        try:
            private(Path(self.baseFilename))
        except Exception:
            os.close(fd)
            raise
        return os.fdopen(fd, "a", encoding="utf-8")


class Blocked(ValueError):
    """Only fixed, nonsecret diagnostic codes are used at this boundary."""


def private(path: Path, *, directory=False):
    info = path.lstat()
    if (
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise Blocked("private_host_storage_required")


def executable(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise Blocked("executable_unavailable")
    return resolved


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic(path: Path, payload: bytes):
    if path.is_symlink():
        raise Blocked("private_host_storage_required")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".aed-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def save(path: Path, value):
    atomic(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def read(path: Path):
    private(path)
    if path.stat().st_size > 65536:
        raise Blocked("host_record_invalid")
    return json.loads(path.read_text())


def environment(docker: str) -> dict[str, str]:
    # Do not inherit DOCKER_HOST, COMPOSE_FILE, PYTHONPATH, credentials or shell setup.
    return {
        "HOME": str(Path.home()),
        "PATH": f"{Path(docker).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_GB.UTF-8",
        "DOCKER_CONFIG": str(Path.home() / ".docker"),
    }


def query(command: list[str], env: dict[str, str], code: str, *, optional=False):
    try:
        result = subprocess.run(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked(code) from None
    if result.returncode and not optional:
        raise Blocked(code)
    return result


def host_timezone() -> str:
    target = str(Path("/etc/localtime").resolve(strict=True))
    return "Europe/London" if target.endswith("/Europe/London") else "other"


def host_supported():
    if sys.platform != "darwin" or os.getuid() == 0:
        raise Blocked("macos_user_session_required")
    if host_timezone() != "Europe/London":
        raise Blocked("host_timezone_must_be_europe_london")


def compose(root: Path, docker: str) -> list[str]:
    overlay = root / "fonts.compose.yaml"
    if overlay.exists() != (root / "fonts").exists():
        raise Blocked("font_overlay_incomplete")
    result = [
        docker,
        "--context",
        "desktop-linux",
        "compose",
        "--env-file",
        "/dev/null",
        "-f",
        str(root / "compose.yaml"),
    ]
    if overlay.exists():
        result += ["-f", str(overlay)]
    return result


def deployment(root: Path, docker: str) -> dict:
    """Validate local context and normalized Compose before any container starts."""
    private(root, directory=True)
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise Blocked("deployment_outside_git_required")
    for name in ("config", "state", "credentials"):
        private(root / name, directory=True)
    expected = (root / "image-id").read_text().strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected):
        raise Blocked("immutable_image_required")
    env = environment(docker)
    base = [docker, "--context", "desktop-linux"]
    context = query(
        [
            *base,
            "context",
            "inspect",
            "desktop-linux",
            "--format",
            "{{.Endpoints.docker.Host}}",
        ],
        env,
        "docker_context_unavailable",
    ).stdout.strip()
    if not context.startswith("unix:///"):
        raise Blocked("local_docker_desktop_context_required")
    query(
        [*base, "info", "--format", "{{.ServerVersion}}"],
        env,
        "docker_daemon_unavailable",
    )
    query([*base, "compose", "version", "--short"], env, "compose_unavailable")
    metadata = json.loads(
        query(
            [*base, "image", "inspect", expected], env, "reviewed_image_unavailable"
        ).stdout
    )[0]
    revision = (
        metadata.get("Config", {})
        .get("Labels", {})
        .get("org.opencontainers.image.revision", "")
    )
    if metadata.get("Id") != expected or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Blocked("image_revision_invalid")
    model = json.loads(
        query(
            [*compose(root, docker), "config", "--format", "json"],
            env,
            "compose_configuration_invalid",
        ).stdout
    )
    services = model.get("services", {})
    if set(services) != {"publisher"}:
        raise Blocked("single_publisher_service_required")
    service = services["publisher"]
    if (
        service.get("image") != expected
        or service.get("pull_policy") != "never"
        or service.get("user") != "10001:10001"
        or service.get("read_only") is not True
        or not service.get("init")
        or service.get("working_dir") != "/opt/aed"
        or service.get("entrypoint")
        or service.get("environment")
        or service.get("ports")
        or service.get("privileged")
        or service.get("network_mode")
        or service.get("restart", "no") != "no"
        or service.get("cap_drop") != ["ALL"]
        or service.get("security_opt") != ["no-new-privileges:true"]
    ):
        raise Blocked("compose_runtime_or_image_changed")
    wanted = {
        "/etc/aed": ("config", True),
        "/var/lib/aed": ("state", False),
        "/var/lib/aed-credentials": ("credentials", False),
    }
    if (root / "fonts.compose.yaml").exists():
        wanted["/usr/local/share/fonts/aed"] = ("fonts", True)
    volumes = service.get("volumes", [])
    if len(volumes) != len(wanted):
        raise Blocked("compose_mounts_invalid")
    for mount in volumes:
        target = mount.get("target")
        if target not in wanted:
            raise Blocked("compose_mounts_invalid")
        folder, readonly = wanted[target]
        if (
            mount.get("type") != "bind"
            or Path(mount["source"]) != root / folder
            or bool(mount.get("read_only")) != readonly
            or mount.get("bind", {}).get("create_host_path", True)
        ):
            raise Blocked("compose_mounts_invalid")
    policy(root)
    config = tomllib.loads((root / "config/aed.toml").read_text())
    if (
        config.get("database_path") != "/var/lib/aed/aed.sqlite3"
        or config.get("assets_path") != "/var/lib/aed/assets"
        or config.get("account_handle") != "@ask.every.day"
    ):
        raise Blocked("authoritative_paths_or_account_invalid")
    database = root / "state/aed.sqlite3"
    private(database)
    files = ["compose.yaml", "image-id", "config/aed.toml", "config/schedule.toml"]
    if (root / "fonts.compose.yaml").exists():
        files.append("fonts.compose.yaml")
    # Auth configuration contains only non-secret settings; contents never enter output.
    if (root / "config/instagram-auth.toml").is_file():
        files.append("config/instagram-auth.toml")
    info = database.stat()
    return {
        "image": expected,
        "revision": revision,
        "database": str(database),
        "database_identity": [info.st_dev, info.st_ino],
        "configuration": {name: sha(root / name) for name in files},
        "compose": digest(model),
    }


def policy(root: Path) -> dict:
    """The private mounted file is the single effective daily schedule."""
    path = root / "config/schedule.toml"
    private(path)
    if path.stat().st_size > 8192:
        raise Blocked("schedule_configuration_invalid")
    value = tomllib.loads(path.read_text())
    if (
        set(value) - {"effective_from"} != set(POLICY)
        or value["timezone"] != "Europe/London"
        or type(value["hour"]) is not int
        or not 0 <= value["hour"] <= 23
        or type(value["minute"]) is not int
        or not 0 <= value["minute"] <= 59
        or value["hour"] * 60 + value["minute"] + 30 > 1440
        or type(value["window_minutes"]) is not int
        or value["window_minutes"] != 30
        or type(value["authorization_days"]) is not int
        or value["authorization_days"] != 30
    ):
        raise Blocked("schedule_requires_london_single_slot_30_minutes_30_days")
    effective = value.get("effective_from")
    if effective is not None:
        try:
            if date.fromisoformat(effective).isoformat() != effective:
                raise ValueError
        except (ValueError, TypeError):
            raise Blocked("schedule_effective_date_invalid") from None
    return value


def daily_time(value: dict) -> str:
    return f"{value['hour']:02}:{value['minute']:02}"


def pending(root: Path):
    if (root / "config/schedule-change.json").exists() or (
        root / "host/schedule-change.json"
    ).exists():
        raise Blocked("schedule_change_incomplete_recover_required")


def next_slot(value: dict, now: datetime) -> datetime:
    """Skip a nonexistent spring minute; choose the first fall occurrence only."""
    local = now.astimezone(LONDON)
    day = max(
        local.date(),
        date.fromisoformat(value.get("effective_from", local.date().isoformat())),
    )
    for _ in range(3):
        candidate = datetime.combine(day, datetime.min.time(), LONDON).replace(
            hour=value["hour"], minute=value["minute"], fold=0
        )
        actual = candidate.astimezone(UTC).astimezone(LONDON)
        if actual == candidate and now.astimezone(UTC) < candidate.astimezone(
            UTC
        ) + timedelta(minutes=1):
            return candidate
        day += timedelta(days=1)
    raise Blocked("next_schedule_unavailable")


def launch_status(label=LABEL) -> dict:
    target = f"gui/{os.getuid()}/{label}"
    result = query(
        ["/bin/launchctl", "print", target],
        environment("/usr/bin/docker"),
        "launchctl_unavailable",
        optional=True,
    )
    overrides = query(
        ["/bin/launchctl", "print-disabled", f"gui/{os.getuid()}"],
        environment("/usr/bin/docker"),
        "launchctl_disabled_status_unavailable",
    )
    match = re.search(
        rf'^\s*"{re.escape(label)}"\s*=>\s*(true|false|enabled|disabled)\s*;?\s*$',
        overrides.stdout,
        re.MULTILINE,
    )
    return {
        "loaded": result.returncode == 0,
        "disabled": match.group(1) in {"true", "disabled"} if match else True,
        "running": result.returncode == 0
        and bool(re.search(r"\bpid = [1-9][0-9]*", result.stdout)),
    }


def competitors(root: Path, docker: str):
    if launch_status(LEGACY)["loaded"]:
        raise Blocked("legacy_launchagent_must_be_unloaded")
    env = environment(docker)
    processes = query(
        ["/bin/ps", "-axo", "command="], env, "process_inspection_failed"
    ).stdout
    patterns = (
        "ask_every_day.scheduler_cli run",
        "ask_every_day.publish_next_cli",
        "ask_every_day.render_next",
        "ask_every_day.batch_render",
        "ask_every_day.content_import",
        "ask_every_day.media_staging",
        "ask_every_day.publication_recovery_cli migrate",
    )
    if any(pattern in processes for pattern in patterns):
        raise Blocked("competing_native_process")
    rows = query(
        [docker, "--context", "desktop-linux", "ps", "-a", "--format", "{{json .}}"],
        env,
        "container_inspection_failed",
    ).stdout.splitlines()
    for line in rows:
        row = json.loads(line)
        if row.get(
            "Names"
        ) == CONTAINER or "com.docker.compose.service=publisher" in row.get(
            "Labels", ""
        ):
            raise Blocked("existing_publisher_container_inspect_do_not_retry")


def container_query(
    root: Path, settings: dict, arguments: list[str], code: str, *, module=False
):
    # Only diagnostics use network isolation; the publisher uses reviewed Compose.
    command = [
        *compose(root, settings["docker"]),
        "-f",
        str(root / "host/offline.compose.json"),
        "run",
        "--rm",
        "--no-deps",
        "-T",
    ]
    if module:
        command += ["--entrypoint", "python", "publisher", "-m", *arguments]
    else:
        command += ["publisher", *arguments, "--data-dir", "/var/lib/aed/data"]
    return json.loads(query(command, environment(settings["docker"]), code).stdout)


def load(root: Path) -> dict:
    private(root / "host", directory=True)
    settings = read(root / "host/settings.json")
    if (
        settings["uid"] != os.getuid()
        or settings["script_sha256"] != sha(Path(__file__))
        or str(executable(Path(settings["python"]))) != settings["python"]
        or str(executable(Path(settings["docker"]))) != settings["docker"]
        or sha(Path(settings["python"])) != settings["python_sha256"]
        or sha(root / "host/desktop_scheduler.py") != settings["script_sha256"]
        or read(root / "host/offline.compose.json")
        != {"services": {"publisher": {"network_mode": "none"}}}
    ):
        raise Blocked("host_runtime_changed_review_required")
    return settings


def binding(root: Path, settings: dict) -> str:
    current = deployment(root, settings["docker"])
    if current != settings["deployment"]:
        raise Blocked("deployment_changed_review_required")
    return digest(settings)


def authorization(root: Path, settings: dict):
    return container_query(
        root, settings, ["schedule-status"], "scheduler_authorization_invalid"
    )


def readiness(root: Path, settings: dict, *, credentials=False) -> dict:
    host_supported()
    pending(root)
    fingerprint = binding(root, settings)
    competitors(root, settings["docker"])
    container_query(
        root,
        settings,
        ["ask_every_day.desktop_validation", "mounts"],
        "container_mount_permissions_invalid",
        module=True,
    )
    container_query(
        root,
        settings,
        ["ask_every_day.canonical_renderer"],
        "canonical_font_invalid",
        module=True,
    )
    history = container_query(
        root, settings, ["publication-status"], "checkpoint_state_invalid"
    )
    if any(row.get("operator_action_required") for row in history["attempts"]):
        raise Blocked("unresolved_publication_reconcile_required")
    preview = container_query(
        root, settings, ["schedule-preview"], "state_content_or_palette_invalid"
    )
    auth = authorization(root, settings)
    auth_path = root / "config/instagram-auth.toml"
    if not auth_path.is_file():
        raise Blocked("auth_configuration_missing")
    credential = {}
    if credentials:
        credential = container_query(
            root,
            settings,
            ["schedule-check-credentials"],
            "credential_configuration_or_store_invalid",
        )
        if credential.get("credential_ready") is not True:
            raise Blocked("credential_configuration_or_store_invalid")
    return {
        "binding": fingerprint,
        "image": settings["deployment"]["image"],
        "revision": settings["deployment"]["revision"],
        "database": settings["deployment"]["database"],
        "timezone": "Europe/London",
        "daily_time": daily_time(policy(root)),
        "window_minutes": 30,
        "mounts": "valid",
        "credential_check": "local_valid" if credentials else "not_requested",
        "identity_verification": "owner_must_verify_separately",
        "credential_expires_at": credential.get("expires_at"),
        "authorization_expires_at": auth.get("expires_at"),
        "queue_count": preview.get("queue_count"),
        "next_post_id": preview.get("next_post_id"),
        "next_queue_position": preview.get("next_queue_position"),
        "last_successful_publication": next(
            (
                row
                for row in reversed(history["attempts"])
                if row.get("definitely_completed")
            ),
            None,
        ),
        "failed_attempt_count": sum(
            row.get("status") == "Failed" for row in history["attempts"]
        ),
        "enabled": auth.get("enabled") is True,
        "authorization_valid": auth.get("authorization_valid") is True,
        "daily_block": preview.get("daily_block"),
    }


def approved(root: Path, fingerprint: str) -> bool:
    path = root / "host/authority.json"
    return path.exists() and read(path).get("binding") == fingerprint


def execution_health(root: Path, report: dict, now: datetime) -> dict:
    """Describe calendar opportunities, not a promise of remote acceptance."""
    local = now.astimezone(LONDON)
    value = policy(root)
    candidate = next_slot(value, now)
    if report.get("daily_block"):
        candidate = next_slot(
            value,
            datetime.combine(
                local.date() + timedelta(days=1), datetime.min.time(), LONDON
            ),
        )
    activation = root / "host/activation.json"
    receipt = root / "host/last-invocation.json"
    last_invocation = None
    try:
        if report["host_armed"]:
            first = date.fromisoformat(read(activation)["first_day"])
            candidate = max(candidate, datetime.combine(first, candidate.timetz()))
        if receipt.exists():
            saved = read(receipt)
            day = date.fromisoformat(saved["local_day"])
            outcome = saved["outcome"]
            if outcome not in OUTCOMES | {"admitted"}:
                raise ValueError
            # Never echo arbitrary receipt text, provider messages or IDs.
            last_invocation = {"local_day": day.isoformat(), "outcome": outcome}
            candidate = max(
                candidate,
                datetime.combine(day + timedelta(days=1), candidate.timetz()),
            )
    except (ValueError, KeyError, TypeError):
        raise Blocked("host_receipt_or_activation_invalid") from None
    candidate = next_slot(value, candidate)
    warnings = []
    eligible = report["status"] == "ready_for_next_schedule"
    for field in ("authorization_expires_at", "credential_expires_at"):
        value = report.get(field)
        if value is None:
            eligible = False
            continue
        try:
            expiry = datetime.fromisoformat(value)
            if expiry.utcoffset() is None:
                raise ValueError
        except (ValueError, TypeError):
            raise Blocked("health_expiry_invalid") from None
        if expiry <= local + timedelta(days=7):
            warnings.append(field.removesuffix("_at") + "_within_7_days")
        if expiry <= max(candidate, local):
            eligible = False
            warnings.append(field.removesuffix("_at") + "_before_next_run")
    if type(report.get("queue_count")) is not int or report["queue_count"] <= 0:
        eligible = False
        warnings.append(
            "queue_empty" if report.get("queue_count") == 0 else "queue_unknown"
        )
    if last_invocation and last_invocation["outcome"] in {"blocked", "admitted"}:
        warnings.append("last_host_invocation_requires_inspection")
    return {
        "next_scheduled_at": candidate.isoformat(),
        "next_eligible_at": candidate.isoformat() if eligible else None,
        "last_host_invocation": last_invocation,
        "warnings": warnings,
        "remote_verification": "not_requested_offline_only",
    }


def diagnose(root: Path, settings: dict, *, credentials=False, now=None):
    report = {
        "mode": "nonpublishing",
        "image": settings["deployment"]["image"],
        "revision": settings["deployment"]["revision"],
        "database": settings["deployment"]["database"],
        "authority": "docker_owner_accepted_revalidation_required"
        if (root / "host/authority.json").exists()
        else "mac_until_explicit_cutover",
        "installed": agent_path().exists(),
        "host_armed": (root / "host/activation.json").exists(),
    }
    try:
        report["launchagent"] = launch_status()
        report["host_timezone"] = host_timezone()
        report.update(readiness(root, settings, credentials=credentials))
        activation = root / "host/activation.json"
        report["host_armed"] = (
            activation.exists()
            and read(activation).get("binding") == report["binding"]
            and agent_path().is_file()
            and agent_path().read_bytes() == plist(root, Path(settings["python"]))
        )
        if approved(root, report["binding"]):
            report["authority"] = "docker_owner_accepted"
        report["diagnostic"] = "passed"
        report["status"] = (
            "publication_already_today"
            if report.get("daily_block")
            else "scheduler_disabled_or_unauthorized"
            if not report["authorization_valid"]
            else "cutover_required"
            if report["authority"] != "docker_owner_accepted"
            else "host_trigger_inactive"
            if not report["host_armed"]
            or not report["launchagent"]["loaded"]
            or report["launchagent"]["disabled"]
            else "credentials_not_checked"
            if not credentials
            else "ready_for_next_schedule"
        )
        report.update(execution_health(root, report, now or datetime.now(UTC)))
        if (
            report["status"] == "ready_for_next_schedule"
            and report["next_eligible_at"] is None
        ):
            report["status"] = "next_run_requirements_unsatisfied"
        return report, 0
    except Blocked as error:
        report.update(diagnostic="blocked", reason=str(error))
        return report, 1


def confirm(challenge: str):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise Blocked("interactive_owner_confirmation_required")
    print(challenge, flush=True)
    if input("> ") != challenge:
        raise Blocked("owner_confirmation_mismatch")


def plist(root: Path, python: Path) -> bytes:
    value = policy(root)
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [
                str(python),
                "-I",
                str(root / "host/desktop_scheduler.py"),
                "run",
                "--directory",
                str(root),
            ],
            "WorkingDirectory": str(root / "host"),
            "StartCalendarInterval": {"Hour": value["hour"], "Minute": value["minute"]},
            "Disabled": True,
            "RunAtLoad": False,
            "KeepAlive": False,
            "ProcessType": "Background",
            "ExitTimeOut": 0,
            "Umask": 0o077,
        },
        sort_keys=True,
    )


def prepare(root: Path, python: Path, docker: Path):
    host_supported()
    python, docker = executable(python), executable(docker)
    check = query(
        [str(python), "-I", str(Path(__file__).resolve()), "self-check"],
        environment(str(docker)),
        "python_or_script_unusable",
    )
    if json.loads(check.stdout) != {"self_check": True}:
        raise Blocked("python_or_script_unusable")
    current = deployment(root, str(docker))
    folder = root / "host"
    folder.mkdir(mode=0o700)  # Never overwrite an installed trigger or receipts.
    atomic(folder / "desktop_scheduler.py", Path(__file__).read_bytes())
    settings = {
        "uid": os.getuid(),
        "python": str(python),
        "python_sha256": sha(python),
        "docker": str(docker),
        "script_sha256": sha(Path(__file__)),
        "deployment": current,
    }
    save(folder / "settings.json", settings)
    save(
        folder / "offline.compose.json",
        {"services": {"publisher": {"network_mode": "none"}}},
    )
    atomic(folder / f"{LABEL}.plist", plist(root, python))
    return {
        "prepared": True,
        "installed": False,
        "authority_accepted": False,
        "enabled": False,
    }


def agent_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def install(root: Path, settings: dict):
    with host_lock(root, "configuration.lock"):
        return _install(root, settings)


def _install(root: Path, settings: dict):
    host_supported()
    if not approved(root, binding(root, settings)):
        raise Blocked("explicit_authority_acceptance_required")
    if launch_status()["loaded"] or agent_path().exists():
        raise Blocked("existing_launchagent_review_required")
    data = (root / "host" / f"{LABEL}.plist").read_bytes()
    if data != plist(root, Path(settings["python"])):
        raise Blocked("launchagent_configuration_changed")
    agent_path().parent.mkdir(parents=True, exist_ok=True)
    query(
        ["/bin/launchctl", "disable", f"gui/{os.getuid()}/{LABEL}"],
        environment(settings["docker"]),
        "launchagent_disable_failed",
    )
    atomic(agent_path(), data)
    return {"installed": True, "enabled": False, "loaded": False}


def activate(root: Path, settings: dict, now: datetime):
    with host_lock(root, "configuration.lock"):
        return _activate(root, settings, now)


def _activate(root: Path, settings: dict, now: datetime):
    report = readiness(root, settings, credentials=True)
    if not approved(root, report["binding"]) or not report["authorization_valid"]:
        raise Blocked("authority_and_scheduler_authorization_required")
    if launch_status()["loaded"]:
        raise Blocked("existing_launchagent_review_required")
    if agent_path().read_bytes() != plist(root, Path(settings["python"])):
        raise Blocked("launchagent_configuration_changed")
    value = policy(root)
    challenge = (
        "ACTIVATE DOCKER DAILY " + daily_time(value) + " " + report["binding"][:16]
    )
    confirm(challenge)
    if readiness(root, settings, credentials=True) != report:
        raise Blocked("activation_preview_changed")
    local = now.astimezone(LONDON)
    first = local.date() + timedelta(
        days=int((local.hour, local.minute) >= (value["hour"], value["minute"]))
    )
    first = max(
        first, date.fromisoformat(value.get("effective_from", first.isoformat()))
    )
    save(
        root / "host/activation.json",
        {"binding": report["binding"], "first_day": first.isoformat()},
    )
    env = environment(settings["docker"])
    target = f"gui/{os.getuid()}/{LABEL}"
    try:
        query(["/bin/launchctl", "enable", target], env, "launchagent_enable_failed")
        query(
            ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent_path())],
            env,
            "launchagent_bootstrap_failed",
        )
    except Blocked:
        (root / "host/activation.json").unlink(missing_ok=True)
        query(
            ["/bin/launchctl", "disable", target],
            env,
            "launchagent_disable_failed",
            optional=True,
        )
        raise
    return {
        "activated": True,
        "first_eligible_day": first.isoformat(),
        "publication_started": False,
    }


def disable(root: Path, settings: dict, *, uninstall=False):
    with host_lock(root, "configuration.lock"):
        return _disable(root, settings, uninstall=uninstall)


def _disable(root: Path, settings: dict, *, uninstall=False):
    host_supported()
    # Stop future admissions first. Never kill or unload an active publication.
    (root / "host/activation.json").unlink(missing_ok=True)
    env = environment(settings["docker"])
    target = f"gui/{os.getuid()}/{LABEL}"
    query(["/bin/launchctl", "disable", target], env, "launchagent_disable_failed")
    status = launch_status()
    if uninstall:
        if status["running"]:
            raise Blocked("active_publication_wait_before_uninstall")
        competitors(root, settings["docker"])
        if status["loaded"]:
            query(
                ["/bin/launchctl", "bootout", target], env, "launchagent_unload_failed"
            )
        agent_path().unlink(missing_ok=True)
    return {
        "host_enabled": False,
        "active_operation_left_running": status["running"],
        "uninstalled": uninstall,
        "application_authorization": "revoke_separately_when_idle",
    }


@contextmanager
def host_lock(root: Path, filename="trigger.lock"):
    path = root / "host" / filename
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private(path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked("host_trigger_overlap") from None
        yield
    finally:
        os.close(fd)


@contextmanager
def event_log(root: Path):
    path = root / "host/host.jsonl"
    for suffix in ("", ".1", ".2", ".3"):
        candidate = Path(str(path) + suffix)
        if candidate.exists() or candidate.is_symlink():
            private(candidate)
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    logger = logging.getLogger("aed.desktop.host")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = PrivateLog(path, maxBytes=1_048_576, backupCount=3)
    logger.addHandler(handler)
    try:
        yield lambda event, **fields: logger.info(
            json.dumps(
                {"event": event, "timestamp": datetime.now(UTC).isoformat(), **fields}
            )
        )
    finally:
        logger.removeHandler(handler)
        handler.close()


def publication(command: list[str], env: dict[str, str]):
    # No timeout, restart, shell or kill. Ignore termination while awaiting the
    # one-shot client; explicit Docker/Meta reconciliation handles uncertain exits.
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, lambda *_: None)
    try:
        process = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        output, _ = process.communicate()
        return process.returncode, output
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_once(root: Path, settings: dict, *, clock=lambda: datetime.now(UTC)):
    with host_lock(root), event_log(root) as event:
        now = clock().astimezone(LONDON)
        day = now.date().isoformat()
        try:
            host_supported()
            pending(root)
            value = policy(root)
            if (
                sha(root / "config/schedule.toml")
                != settings["deployment"]["configuration"]["config/schedule.toml"]
            ):
                raise Blocked("deployment_changed_review_required")
            activation_path = root / "host/activation.json"
            if not activation_path.exists():
                event("host_trigger_disabled", local_day=day)
                return {"outcome": "host_disabled"}, 0
            activation = read(activation_path)
            if (
                day
                < max(
                    activation["first_day"],
                    value.get("effective_from", activation["first_day"]),
                )
                or (now.hour, now.minute) != (value["hour"], value["minute"])
                or now.fold
            ):
                event("host_trigger_missed_start", local_day=day)
                return {"outcome": "missed_start_no_backfill"}, 0
            if agent_path().exists() and agent_path().read_bytes() != plist(
                root, Path(settings["python"])
            ):
                raise Blocked("launchagent_configuration_changed")
            receipt = root / "host/last-invocation.json"
            if receipt.exists() and read(receipt)["local_day"] >= day:
                event("host_trigger_duplicate", local_day=day)
                return {"outcome": "already_invoked_today"}, 0
            # Persist admission before diagnostics/launch. Failure or interruption
            # consumes this host invocation; SQLite remains publication authority.
            save(receipt, {"local_day": day, "outcome": "admitted"})
            report = readiness(root, settings, credentials=True)
            if (
                not approved(root, report["binding"])
                or activation["binding"] != report["binding"]
                or not report["authorization_valid"]
            ):
                raise Blocked("authority_and_scheduler_authorization_required")
            if report["daily_block"]:
                event("host_trigger_daily_guard", local_day=day)
                return {"outcome": "daily_guard"}, 0
            # A delayed preflight must not dispatch a wake/catch-up publication.
            current = clock().astimezone(LONDON)
            if current.date() != now.date() or (current.hour, current.minute) != (
                value["hour"],
                value["minute"],
            ):
                raise Blocked("start_minute_elapsed_no_backfill")
            if read(activation_path) != activation:
                raise Blocked("host_activation_changed")
            command = [
                *compose(root, settings["docker"]),
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--name",
                CONTAINER,
                "publisher",
                "schedule-run",
                "--data-dir",
                "/var/lib/aed/data",
                "--log-file",
                "/var/lib/aed/scheduler.jsonl",
            ]
            event("host_publication_dispatch", local_day=day)
            code, output = publication(command, environment(settings["docker"]))
            try:
                value = json.loads(output)
                outcome = value.get("outcome")
                if outcome not in OUTCOMES or value.get("local_day") != day:
                    raise ValueError
                attempt_id = value.get("attempt_id")
                post_id = value.get("post_id")
                if outcome == "published" and (
                    code != 0
                    or type(attempt_id) is not int
                    or attempt_id <= 0
                    or not isinstance(post_id, str)
                    or not re.fullmatch(r"AED-[0-9]{4}", post_id)
                ):
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise Blocked(
                    "container_result_unverified_inspect_checkpoint"
                ) from None
            if outcome == "blocked":
                state = value.get("publication_state")
                category = value.get("failure_category")
                if (
                    type(value.get("schema_version")) is not int
                    or value.get("schema_version") != 1
                    or code == 0
                    or state not in ("no_attempt", "reconciliation_required", "unknown")
                    or category
                    not in (
                        "authentication",
                        "configuration",
                        "transient",
                        "api_rejected",
                        "preflight",
                        "unexpected",
                    )
                    or (
                        state == "no_attempt"
                        and any(
                            value.get(k) is not None
                            for k in ("publication_id", "container_id", "media_id")
                        )
                    )
                ):
                    raise Blocked("container_result_unverified_inspect_checkpoint")
                reason = (
                    "preflight_failed"
                    if state == "no_attempt"
                    else "publication_requires_reconciliation"
                    if state == "reconciliation_required"
                    else "container_application_failed_inspect_checkpoint"
                )
                details: dict[str, object] = {
                    "publication_state": state,
                    "failure_category": category,
                }
                publication_id = value.get("publication_id")
                if type(publication_id) is int and publication_id > 0:
                    details["publication_id"] = publication_id
                for key in ("container_id", "media_id"):
                    provider_id = value.get(key)
                    if isinstance(provider_id, str) and re.fullmatch(
                        r"[0-9]{1,128}", provider_id
                    ):
                        details[key] = provider_id
                diagnostic = value.get("diagnostics")
                if isinstance(diagnostic, dict):
                    for key in ("http_status", "meta_error_code", "meta_error_subcode"):
                        number = diagnostic.get(key)
                        if type(number) is int and 0 <= number <= 2**31 - 1:
                            details[key] = number
                event(
                    "host_publication_failed",
                    reason=reason,
                    local_day=day,
                    exit_code=code,
                    **details,
                )
                save(
                    receipt,
                    {
                        "local_day": day,
                        "outcome": "blocked",
                        "reason": reason,
                        **details,
                    },
                )
                return {
                    "outcome": "blocked",
                    "reason": reason,
                    "local_day": day,
                    **details,
                }, 1
            event(
                "host_publication_result",
                outcome=outcome,
                exit_code=code,
                local_day=day,
                attempt_id=value.get("attempt_id")
                if type(value.get("attempt_id")) is int and value["attempt_id"] > 0
                else None,
                post_id=value.get("post_id")
                if isinstance(value.get("post_id"), str)
                and re.fullmatch(r"AED-[0-9]{4}", value["post_id"])
                else None,
            )
            save(receipt, {"local_day": day, "outcome": outcome, "exit_code": code})
            return {
                "outcome": outcome,
                "local_day": day,
            }, 0 if code == 0 and outcome != "blocked" else 1
        except Blocked as error:
            event("host_trigger_blocked", reason=str(error), local_day=day, exit_code=1)
            return {"outcome": "blocked", "reason": str(error)}, 1
        except Exception:
            event(
                "host_trigger_blocked",
                reason="host_operation_failed_inspect_checkpoint",
                local_day=day,
                exit_code=1,
            )
            return {
                "outcome": "blocked",
                "reason": "host_operation_failed_inspect_checkpoint",
            }, 1


def schedule_show(root: Path, settings: dict, *, now=None):
    pending(root)
    fingerprint = binding(root, settings)
    value = policy(root)
    installed = agent_path().exists()
    matches = not installed or agent_path().read_bytes() == plist(
        root, Path(settings["python"])
    )
    activation = root / "host/activation.json"
    status = launch_status()
    try:
        auth = authorization(root, settings)
    except Blocked:
        auth = {
            "authorization_valid": False,
            "reason": "reauthorization_or_diagnostics_required",
        }
    report = {
        "status": "host_trigger_inactive",
        "host_armed": activation.exists(),
        "queue_count": 0,
        "daily_block": auth.get("daily_block"),
    }
    health = execution_health(root, report, now or datetime.now(UTC))
    return {
        "daily_time": daily_time(value),
        "timezone": value["timezone"],
        "window_minutes": value["window_minutes"],
        "effective_from": value.get("effective_from"),
        "authorization_days": value["authorization_days"],
        "schedule_file": str(root / "config/schedule.toml"),
        "launchagent_matches": matches,
        "launchagent": status,
        "authority_accepted": approved(root, fingerprint),
        "authorization": auth,
        "next_scheduled_at": health["next_scheduled_at"],
        "last_host_invocation": health["last_host_invocation"],
        "mode": "nonpublishing",
    }


def parse_time(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", value):
        raise Blocked("daily_time_requires_HH_MM")
    hour, minute = map(int, value.split(":"))
    if hour * 60 + minute + 30 > 1440:
        raise Blocked("daily_window_must_end_before_midnight")
    return hour, minute


def schedule_receipt(root: Path, day: date, *, today: bool):
    receipt = root / "host/last-invocation.json"
    if not receipt.exists():
        return
    saved = read(receipt)
    previous = date.fromisoformat(saved["local_day"])
    outcome = saved["outcome"]
    if outcome not in OUTCOMES | {"admitted"}:
        raise Blocked("host_receipt_invalid")
    if previous >= day and outcome in {"blocked", "admitted"}:
        raise Blocked("admitted_failure_requires_operator_investigation")
    if previous > day or (today and previous == day):
        raise Blocked("host_already_admitted_today")


@contextmanager
def change_participant(root: Path, settings: dict, hour: int, minute: int, day: date):
    command = [
        *compose(root, settings["docker"]),
        "-f",
        str(root / "host/offline.compose.json"),
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        CONTAINER,
        "--entrypoint",
        "python",
        "publisher",
        "-m",
        "ask_every_day.schedule_change",
        "--hour",
        str(hour),
        "--minute",
        str(minute),
        "--effective-day",
        day.isoformat(),
    ]
    process = subprocess.Popen(
        command,
        env=environment(settings["docker"]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    stdout, stdin = process.stdout, process.stdin
    assert stdout is not None and stdin is not None

    def exchange(message=None):
        if message is not None:
            stdin.write(message + "\n")
            stdin.flush()
        if not select.select([stdout], [], [], 120)[0]:
            raise Blocked("schedule_participant_timeout_inspect_container")
        value = json.loads(stdout.readline(8192))
        if value.get("phase") not in {"ready", "committed", "aborted"}:
            reason = value.get("reason", "schedule_participant_failed")
            if not isinstance(reason, str) or not re.fullmatch(
                r"[a-z_]{1,100}", reason
            ):
                reason = "schedule_participant_failed"
            raise Blocked(reason)
        return value

    try:
        yield exchange
    finally:
        # EOF aborts a waiting participant. Never kill any publisher container.
        stdin.close()
        stdout.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # Its held lock/name blocks all further changes pending inspection.


def reload_agent(root: Path, settings: dict, status: dict):
    env = environment(settings["docker"])
    target = f"gui/{os.getuid()}/{LABEL}"
    query(["/bin/launchctl", "disable", target], env, "launchagent_disable_failed")
    if launch_status()["running"]:
        raise Blocked("active_publication_wait_before_schedule_change")
    if launch_status()["loaded"]:
        query(["/bin/launchctl", "bootout", target], env, "launchagent_unload_failed")
    if status["loaded"]:
        if not status["disabled"]:
            query(
                ["/bin/launchctl", "enable", target], env, "launchagent_enable_failed"
            )
        query(
            ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent_path())],
            env,
            "launchagent_bootstrap_failed",
        )
    elif not status["disabled"]:
        query(["/bin/launchctl", "enable", target], env, "launchagent_enable_failed")
    actual = launch_status()
    if actual["loaded"] != status["loaded"] or actual["disabled"] != status["disabled"]:
        raise Blocked("launchagent_activation_unverified")


def change_snapshot(root: Path) -> dict:
    names = [
        "config/schedule.toml",
        "host/settings.json",
        "host/authority.json",
        "host/activation.json",
        f"host/{LABEL}.plist",
    ]
    files = {}
    for name in names:
        path = root / name
        if path.exists():
            private(path)
            files[name] = path.read_text()
        else:
            files[name] = None
    installed = agent_path()
    if installed.exists():
        private(installed)
    return {
        "files": files,
        "installed": installed.read_text() if installed.exists() else None,
    }


def restore_change(root: Path, saved: dict):
    allowed = {
        "config/schedule.toml",
        "host/settings.json",
        "host/authority.json",
        "host/activation.json",
        f"host/{LABEL}.plist",
    }
    if set(saved["files"]) != allowed:
        raise Blocked("schedule_journal_invalid")
    for name, value in saved["files"].items():
        if value is None:
            (root / name).unlink(missing_ok=True)
        else:
            atomic(root / name, value.encode())
    if saved["installed"] is not None:
        atomic(agent_path(), saved["installed"].encode())


def clear_change(root: Path):
    (root / "config/schedule-change.json").unlink(missing_ok=True)
    (root / "host/schedule-change.json").unlink(missing_ok=True)


def schedule_set(
    root: Path,
    settings: dict,
    value: str,
    *,
    today=False,
    clock=lambda: datetime.now(UTC),
):
    with (
        host_lock(root, "configuration.lock"),
        host_lock(root),
        event_log(root) as event,
    ):
        try:
            pending(root)
            host_supported()
            settings = load(root)  # Re-read under lock, including a competing edit.
            fingerprint = binding(root, settings)
            if not approved(root, fingerprint):
                raise Blocked("explicit_authority_acceptance_required")
            competitors(root, settings["docker"])
            old = policy(root)
            hour, minute = parse_time(value)
            local = clock().astimezone(LONDON)
            day = local.date() + timedelta(days=0 if today else 1)
            if (hour, minute) == (old["hour"], old["minute"]) and not today:
                return {"changed": False, "daily_time": daily_time(old)}
            target = datetime.combine(day, datetime.min.time(), LONDON).replace(
                hour=hour, minute=minute, fold=0
            )
            if (
                target.astimezone(UTC) <= clock().astimezone(UTC)
                or target.astimezone(UTC).astimezone(LONDON) != target
            ):
                raise Blocked("new_schedule_must_be_a_future_existing_minute")
            schedule_receipt(root, local.date(), today=today)
            status = launch_status()
            if status["running"]:
                raise Blocked("active_publication_wait_before_schedule_change")
            old_plist = plist(root, Path(settings["python"]))
            if (
                (root / "host" / f"{LABEL}.plist").read_bytes() != old_plist
                or (agent_path().exists() and agent_path().read_bytes() != old_plist)
                or (status["loaded"] and not agent_path().exists())
            ):
                raise Blocked("launchagent_configuration_changed")
            activation = root / "host/activation.json"
            if activation.exists() and read(activation)["binding"] != fingerprint:
                raise Blocked("host_activation_changed")
            event(
                "schedule_change_validated",
                previous_time=daily_time(old),
                daily_time=value,
                effective_day=day.isoformat(),
            )
            confirm(
                f"SET DOCKER DAILY {value} Europe/London FROM {day.isoformat()} {fingerprint[:16]}"
            )
            if load(root) != settings or binding(root, settings) != fingerprint:
                raise Blocked("schedule_change_preview_changed")
            saved = change_snapshot(root)
            with change_participant(root, settings, hour, minute, day) as exchange:
                ready = exchange()
                if ready.get("phase") != "ready":
                    raise Blocked("schedule_participant_not_ready")
                preserved = ready.get("authorization_preserved") is True
                # Journal is a durable fail-closed barrier, never publication state.
                save(root / "host/schedule-change.json", saved)
                save(root / "config/schedule-change.json", {"in_progress": True})
                commit_sent = False
                try:
                    new = {
                        **old,
                        "hour": hour,
                        "minute": minute,
                        "effective_from": day.isoformat(),
                    }
                    atomic(
                        root / "config/schedule.toml",
                        (
                            f'timezone="Europe/London"\nhour={hour}\nminute={minute}\n'
                            "window_minutes=30\nauthorization_days=30\n"
                            f'effective_from="{day.isoformat()}"\n'
                        ).encode(),
                    )
                    updated = {
                        **settings,
                        "deployment": deployment(root, settings["docker"]),
                    }
                    save(root / "host/settings.json", updated)
                    authority = read(root / "host/authority.json")
                    save(
                        root / "host/authority.json",
                        {**authority, "binding": digest(updated)},
                    )
                    if activation.exists():
                        if preserved:
                            current = read(activation)
                            save(
                                activation,
                                {
                                    **current,
                                    "binding": digest(updated),
                                    "first_day": day.isoformat(),
                                },
                            )
                        else:
                            activation.unlink()
                    payload = plist(root, Path(settings["python"]))
                    atomic(root / "host" / f"{LABEL}.plist", payload)
                    if agent_path().exists():
                        atomic(agent_path(), payload)
                        reload_agent(
                            root,
                            updated,
                            status
                            if preserved
                            else {"loaded": False, "disabled": True},
                        )
                    if (
                        clock().astimezone(UTC) >= target.astimezone(UTC)
                        or policy(root) != new
                    ):
                        raise Blocked("schedule_change_start_elapsed")
                    commit_sent = True
                    result = exchange("commit")
                    if result.get("phase") != "committed":
                        raise Blocked("schedule_change_commit_unverified")
                    clear_change(root)
                except BaseException:
                    event("schedule_change_rollback_started")
                    try:
                        restore_change(root, saved)
                        # A lost commit acknowledgement requires fresh authorization.
                        if commit_sent:
                            activation.unlink(missing_ok=True)
                        if agent_path().exists():
                            reload_agent(
                                root,
                                settings,
                                status
                                if not commit_sent
                                else {"loaded": False, "disabled": True},
                            )
                        clear_change(root)
                        event("schedule_change_rolled_back", daily_time=daily_time(old))
                    except BaseException:
                        activation.unlink(missing_ok=True)
                        event(
                            "schedule_change_rollback_failed",
                            reason="schedule_recover_required",
                        )
                        raise Blocked(
                            "schedule_rollback_incomplete_recover_required"
                        ) from None
                    raise
            event(
                "schedule_change_activated",
                daily_time=value,
                effective_day=day.isoformat(),
                authorization_preserved=preserved,
            )
            return {
                "changed": True,
                "daily_time": value,
                "timezone": "Europe/London",
                "effective_day": day.isoformat(),
                "authorization_preserved": preserved,
                "reauthorization_reason": ready.get("reauthorization_reason"),
                "publication_started": False,
            }
        except Blocked as error:
            event("schedule_change_rejected", reason=str(error))
            raise


def schedule_recover(root: Path, settings: dict):
    """Restore pre-change host files after interruption; never resume a trigger."""
    with (
        host_lock(root, "configuration.lock"),
        host_lock(root),
        event_log(root) as event,
    ):
        host_supported()
        competitors(root, settings["docker"])
        if launch_status()["running"]:
            raise Blocked("active_publication_wait_before_schedule_recovery")
        saved = read(root / "host/schedule-change.json")
        confirm("ROLL BACK INCOMPLETE SCHEDULE CHANGE AND LEAVE HOST DISABLED")
        restore_change(root, saved)
        (root / "host/activation.json").unlink(missing_ok=True)
        if agent_path().exists():
            reload_agent(root, settings, {"loaded": False, "disabled": True})
        clear_change(root)
        event("schedule_change_recovered", host_enabled=False)
        return {
            "recovered": True,
            "host_enabled": False,
            "next_step": "schedule-status_then_reauthorize_if_invalid_then_activate",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "schedule-show",
            "schedule-set",
            "schedule-recover",
            "prepare",
            "diagnose",
            "accept-authority",
            "install",
            "activate",
            "disable",
            "uninstall",
            "run",
            "self-check",
        ),
    )
    parser.add_argument("time", nargs="?")
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--docker", type=Path, default=Path("/usr/local/bin/docker"))
    parser.add_argument("--check-credentials", action="store_true")
    args = parser.parse_args(argv)
    if (args.time is not None or args.today) and args.command != "schedule-set":
        parser.error("time/--today require schedule-set")
    if args.command == "schedule-set" and args.time is None:
        parser.error("schedule-set requires HH:MM")
    os.umask(0o077)
    code = 0
    try:
        if args.command == "self-check":
            print(json.dumps({"self_check": sys.version_info >= (3, 13)}))
            return 0
        if (
            args.directory is None
            or not args.directory.is_absolute()
            or args.directory.is_symlink()
        ):
            raise Blocked("absolute_private_deployment_required")
        root = args.directory.resolve(strict=True)
        if args.command == "prepare":
            output = prepare(root, args.python, args.docker)
        else:
            settings = load(root)
            if args.command == "schedule-show":
                output = schedule_show(root, settings)
            elif args.command == "schedule-set":
                assert args.time is not None
                output = schedule_set(root, settings, args.time, today=args.today)
            elif args.command == "schedule-recover":
                output = schedule_recover(root, settings)
            elif args.command == "run":
                output, code = run_once(root, settings)
            elif args.command == "diagnose":
                output, code = diagnose(
                    root, settings, credentials=args.check_credentials
                )
            elif args.command == "accept-authority":
                if launch_status()["loaded"] or agent_path().exists():
                    raise Blocked("uninstall_previous_docker_agent_before_cutover")
                output = readiness(root, settings, credentials=True)
                if output["enabled"]:
                    raise Blocked("disable_old_authorization_before_cutover")
                confirm(
                    "ACCEPT SOLE DOCKER AUTHORITY VERIFIED @ask.every.day "
                    + output["binding"][:16]
                )
                if readiness(root, settings, credentials=True) != output:
                    raise Blocked("authority_preview_changed")
                save(
                    root / "host/authority.json",
                    {
                        "binding": output["binding"],
                        "accepted_at": datetime.now(UTC).isoformat(),
                        "identity_and_latest_state": "explicit_owner_attestation",
                    },
                )
                output = {
                    "authority": "docker_owner_accepted",
                    "publication_enabled": False,
                }
            elif args.command == "install":
                output = install(root, settings)
            elif args.command == "activate":
                output = activate(root, settings, datetime.now(UTC))
            else:
                output = disable(root, settings, uninstall=args.command == "uninstall")
    except Blocked as error:
        output, code = {"outcome": "blocked", "reason": str(error)}, 1
    except Exception:
        output, code = (
            {"outcome": "blocked", "reason": "host_configuration_or_runtime_invalid"},
            1,
        )
    if args.command == "run" and code and args.directory is not None:
        try:
            private(args.directory / "host", directory=True)
            with event_log(args.directory) as event:
                event("host_command_failed", reason=output["reason"], exit_code=code)
        except Exception:
            pass  # stdout still reports failure; never create an unsafe log path.
    print(json.dumps(output, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
