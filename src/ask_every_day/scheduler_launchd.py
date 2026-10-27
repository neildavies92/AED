"""Generate, never install, a user LaunchAgent for the portable daily command."""

import argparse
import os
import plistlib
import tempfile
from pathlib import Path

from ask_every_day.scheduler_policy import ScheduleConfig, ScheduleError, load_schedule


def launch_agent(
    python: Path,
    working_directory: Path,
    config: Path,
    schedule_config: Path,
    auth_config: Path,
    data_directory: Path,
    log_file: Path,
    schedule: ScheduleConfig,
) -> bytes:
    paths = (
        python,
        working_directory,
        config,
        schedule_config,
        auth_config,
        data_directory,
        log_file,
    )
    if any(not p.is_absolute() or "\x00" in str(p) for p in paths):
        raise ScheduleError("launchd_absolute_paths_required")
    if (
        not python.is_file()
        or not os.access(python, os.X_OK)
        or not working_directory.is_dir()
    ):
        raise ScheduleError("launchd_runtime_unavailable")
    return plistlib.dumps(
        {
            "Label": "com.ask-every-day.publisher",
            "ProgramArguments": [
                "/bin/sh",
                str(working_directory / "scripts" / "with-cairo.sh"),
                str(python),
                "-m",
                "ask_every_day.scheduler_cli",
                "run",
                "--config",
                str(config),
                "--schedule-config",
                str(schedule_config),
                "--auth-config",
                str(auth_config),
                "--data-dir",
                str(data_directory),
                "--log-file",
                str(log_file),
            ],
            "WorkingDirectory": str(working_directory),
            "EnvironmentVariables": {
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                "PYTHONUNBUFFERED": "1",
                "TZ": "Europe/London",
            },
            # Frequent trigger tolerates host timezone differences. Application owns cadence/DST.
            "StartInterval": min(300, schedule.window_minutes * 30),
            "ProcessType": "Background",
            "Umask": 0o077,
        },
        sort_keys=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an inactive LaunchAgent")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--working-directory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--schedule-config", type=Path, required=True)
    parser.add_argument("--auth-config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("output/com.ask-every-day.publisher.plist")
    )
    args = parser.parse_args()
    try:
        data = launch_agent(
            args.python,
            args.working_directory,
            args.config,
            args.schedule_config,
            args.auth_config,
            args.data_dir,
            args.log_file,
            load_schedule(args.schedule_config),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=args.output.parent, prefix=".aed-launchd-"
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            os.replace(temporary, args.output)
        finally:
            Path(temporary).unlink(missing_ok=True)
        print("Generated inactive LaunchAgent: " + str(args.output))
    except Exception:
        parser.exit(1, "launchd_generation_failed; see docs/scheduler.md.\n")


if __name__ == "__main__":
    main()
