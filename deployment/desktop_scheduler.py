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
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

LABEL = "com.ask-every-day.docker-publisher"
LEGACY = "com.ask-every-day.publisher"
CONTAINER = "aed-desktop-publisher"
LONDON = ZoneInfo("Europe/London")
POLICY = dict(
    timezone="Europe/London",
    hour=19,
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
    schedule = tomllib.loads((root / "config/schedule.toml").read_text())
    if schedule != POLICY:
        raise Blocked("policy_must_be_1900_1930_london_30_days")
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
        "window": "19:00–19:30",
        "mounts": "valid",
        "credential_check": "local_valid" if credentials else "not_requested",
        "identity_verification": "owner_must_verify_separately",
        "enabled": auth.get("enabled") is True,
        "authorization_valid": auth.get("authorization_valid") is True,
        "daily_block": preview.get("daily_block"),
    }


def approved(root: Path, fingerprint: str) -> bool:
    path = root / "host/authority.json"
    return path.exists() and read(path).get("binding") == fingerprint


def diagnose(root: Path, settings: dict, *, credentials=False):
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
            else "ready_for_next_1900"
        )
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
            "StartCalendarInterval": {"Hour": 19, "Minute": 0},
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
    report = readiness(root, settings, credentials=True)
    if not approved(root, report["binding"]) or not report["authorization_valid"]:
        raise Blocked("authority_and_scheduler_authorization_required")
    if launch_status()["loaded"]:
        raise Blocked("existing_launchagent_review_required")
    if agent_path().read_bytes() != plist(root, Path(settings["python"])):
        raise Blocked("launchagent_configuration_changed")
    challenge = "ACTIVATE DOCKER DAILY 19:00 " + report["binding"][:16]
    confirm(challenge)
    if readiness(root, settings, credentials=True) != report:
        raise Blocked("activation_preview_changed")
    local = now.astimezone(LONDON)
    first = local.date() + timedelta(days=int((local.hour, local.minute) >= (19, 0)))
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
def host_lock(root: Path):
    path = root / "host/trigger.lock"
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
            activation_path = root / "host/activation.json"
            if not activation_path.exists():
                event("host_trigger_disabled", local_day=day)
                return {"outcome": "host_disabled"}, 0
            activation = read(activation_path)
            if day < activation["first_day"] or (now.hour, now.minute) != (19, 0):
                event("host_trigger_missed_start", local_day=day)
                return {"outcome": "missed_start_no_backfill"}, 0
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
                19,
                0,
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
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
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--docker", type=Path, default=Path("/usr/local/bin/docker"))
    parser.add_argument("--check-credentials", action="store_true")
    args = parser.parse_args(argv)
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
            if args.command == "run":
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
