"""One explicit local Compose command using the same optional font overlay."""

import argparse
import os
import subprocess
from pathlib import Path


def compose_prefix(directory: Path) -> tuple[list[str], bool]:
    if not (directory / "compose.yaml").is_file():
        raise ValueError("deployment_missing")
    overlay = directory / "fonts.compose.yaml"
    fonts = directory / "fonts"
    if fonts.exists() != overlay.exists():
        raise ValueError("font_overlay_and_directory_required_together")
    command = [
        "docker",
        "--context",
        "desktop-linux",
        "compose",
        "--env-file",
        "/dev/null",
        "-f",
        str(directory / "compose.yaml"),
    ]
    if overlay.exists():
        command += ["-f", str(overlay)]
    return command, overlay.exists()


def run(
    directory: Path,
    arguments: list[str],
    *,
    interactive: bool = False,
    review_directory: Path | None = None,
) -> None:
    prefix, licensed = compose_prefix(directory.resolve())
    if not arguments:
        raise ValueError("explicit_command_required")
    env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "DOCKER_CONFIG": str(Path.home() / ".docker"),
    }
    endpoint = subprocess.run(
        [
            "docker",
            "--context",
            "desktop-linux",
            "context",
            "inspect",
            "desktop-linux",
            "--format",
            "{{.Endpoints.docker.Host}}",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if not endpoint.startswith("unix:///"):
        raise ValueError("local_docker_desktop_context_required")
    if licensed:
        subprocess.run(
            [
                *prefix,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "python",
                "publisher",
                "-m",
                "ask_every_day.font_review",
                "inventory",
                "--require-mounted",
            ],
            check=True,
            capture_output=True,
            env=env,
        )
    base = [*prefix, "run", "--rm", "--no-deps", "-it" if interactive else "-T"]
    if arguments[0] in {"font-render", "font-inventory"}:
        if arguments[0] == "font-render":
            if review_directory is None:
                raise ValueError("private_review_directory_required")
            root = review_directory.resolve()
            if any((p / ".git").exists() for p in (root, *root.parents)):
                raise ValueError("review_outside_git_required")
            if (
                root.is_symlink()
                or not root.is_dir()
                or root.stat().st_mode & 0o777 != 0o700
            ):
                raise ValueError("private_review_directory_required")
            base += ["--mount", f"type=bind,src={root},dst=/review"]
        command = [
            *base,
            "--entrypoint",
            "python",
            "publisher",
            "-m",
            "ask_every_day.font_review",
            arguments[0].removeprefix("font-"),
            *arguments[1:],
        ]
        if licensed:
            command.append("--require-mounted")
    else:
        command = [*base, "publisher", *arguments]
    subprocess.run(command, check=True, env=env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--review-directory", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        run(
            args.directory,
            args.arguments,
            interactive=args.interactive,
            review_directory=args.review_directory,
        )
    except Exception:
        parser.exit(
            1,
            "Local command blocked; check matching font overlay and deployment configuration.\n",
        )


if __name__ == "__main__":
    main()
