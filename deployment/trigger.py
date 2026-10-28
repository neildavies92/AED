"""Root-owned host trigger; one container, bounded logs, no publication policy."""

import fcntl
import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/srv/aed")


def eligible(command: str, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo("Europe/London"))
    return local.hour == 21 and (
        0 <= local.minute < 30 if command == "run" else local.minute >= 35
    )


def invoke(command: str) -> int:
    if command not in {"run", "monitor"}:
        return 2
    if not eligible(command, datetime.now().astimezone()):
        return 0
    logger = logging.getLogger("aed.host")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        ROOT / "logs/host.jsonl", maxBytes=1_048_576, backupCount=3
    )
    logger.addHandler(handler)
    result = 1
    try:
        with (ROOT / "trigger.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                result = 0
                logger.info(
                    json.dumps({"event": "host_trigger_overlap", "operation": command})
                )
                return 0
            expected = (ROOT / "image-id").read_text().strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected):
                raise ValueError
            # Pin by immutable local ID, not a mutable tag or automatic pull/build.
            compose = ["/usr/bin/docker", "compose", "-f", str(ROOT / "compose.yaml")]
            if (ROOT / "fonts.compose.yaml").exists():
                compose += ["-f", str(ROOT / "fonts.compose.yaml")]
            image = subprocess.run(
                [*compose, "config", "--images"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if image != expected:
                raise ValueError
            operation = "schedule-run" if command == "run" else "monitor"
            arguments = [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--name",
                "aed-daily",
                "publisher",
                operation,
                "--data-dir",
                "/var/lib/aed/data",
            ]
            if command == "run":
                arguments += ["--log-file", "/var/lib/aed/scheduler.jsonl"]
            # No timeout/retry: killing a POST can leave an uncertain outcome. A stuck
            # named container blocks overlap and demands operator inspection.
            process = subprocess.run(arguments, capture_output=True, text=True)
            # Application summaries are safe; raw Docker errors/tracebacks are not retained.
            try:
                # Scheduler stdout is a single pretty-printed JSON document.
                value = json.loads(process.stdout)
                if isinstance(value, dict):
                    logger.info(
                        json.dumps(
                            {
                                "event": "host_result",
                                "operation": command,
                                "healthy": value.get("healthy"),
                                "outcome": value.get("outcome"),
                                "local_day": value.get("local_day"),
                                "unresolved": value.get("unresolved"),
                            }
                        )
                    )
            except ValueError:
                pass
            result = process.returncode
    except Exception:
        result = 1
    finally:
        logger.info(
            json.dumps(
                {
                    "event": "host_trigger_exit",
                    "operation": command,
                    "exit_code": result,
                    "timestamp": datetime.now().astimezone().isoformat(),
                }
            )
        )
        handler.close()
        logger.removeHandler(handler)
    if result:
        # Local journal notification; no external messaging or blind retry.
        subprocess.run(
            [
                "/usr/bin/logger",
                "-p",
                "daemon.err",
                "-t",
                "aed",
                "AED "
                + command
                + " failed; inspect /srv/aed/logs/host.jsonl and scheduler.jsonl",
            ],
            check=False,
        )
    return result


if __name__ == "__main__":
    import os

    os.umask(0o077)
    raise SystemExit(invoke(sys.argv[1] if len(sys.argv) == 2 else ""))
