"""Invoke the reviewed installed host schedule CLI; no credential access."""

import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("operation", choices=("show", "set", "recover"))
    parser.add_argument("time", nargs="?")
    parser.add_argument("--today", action="store_true")
    args = parser.parse_args()
    if args.operation == "set" and not args.time:
        parser.error("set requires HH:MM")
    if args.operation != "set" and (args.time or args.today):
        parser.error("time/--today require set")
    # Read only the non-secret installed interpreter path; the target checks all
    # runtime fingerprints and permissions again before any change.
    root = args.directory.resolve(strict=True)
    settings = json.loads((root / "host/settings.json").read_text())
    command = [
        settings["python"],
        "-I",
        str(root / "host/desktop_scheduler.py"),
        "schedule-" + args.operation,
        "--directory",
        str(root),
    ]
    if args.time:
        command.append(args.time)
    if args.today:
        command.append("--today")
    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
