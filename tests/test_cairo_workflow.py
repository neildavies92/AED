"""Exercise the command helper with fake host commands, without Homebrew."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).parents[1] / "scripts/with-cairo.sh"


def command(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def host(tmp_path: Path, platform: str = "Darwin") -> dict[str, str]:
    command(tmp_path / "uname", f"echo '{platform}'\n")
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path)
    environment.pop("DYLD_FALLBACK_LIBRARY_PATH", None)
    environment.pop("AED_CAIRO_FALLBACK", None)
    return environment


def run(
    environment: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(HELPER), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def brew(tmp_path: Path) -> tuple[Path, Path]:
    cairo = tmp_path / "custom cairo"
    libffi = tmp_path / "custom libffi"
    for prefix in (cairo, libffi):
        (prefix / "lib").mkdir(parents=True)
    command(
        tmp_path / "brew",
        f"""[ "$1" = --prefix ] || exit 2
case "$2" in
  cairo) echo '{cairo}' ;;
  libffi) echo '{libffi}' ;;
  *) exit 2 ;;
esac
""",
    )
    return cairo, libffi


def test_darwin_discovers_kegs_and_preserves_existing_fallback(tmp_path: Path) -> None:
    environment = host(tmp_path)
    cairo, libffi = brew(tmp_path)
    # Non-DYLD transport preserves Make values through macOS's /bin/sh startup.
    environment["AED_CAIRO_FALLBACK"] = "/custom/existing:/another/library"
    result = run(
        environment,
        sys.executable,
        "-c",
        "import os,json; print(json.dumps(dict(path=os.environ.get('DYLD_FALLBACK_LIBRARY_PATH'), transport=os.environ.get('AED_CAIRO_FALLBACK'))))",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "path": f"{cairo}/lib:{libffi}/lib:/custom/existing:/another/library",
        "transport": None,
    }


def test_darwin_without_existing_fallback(tmp_path: Path) -> None:
    environment = host(tmp_path)
    cairo, libffi = brew(tmp_path)
    result = run(
        environment,
        sys.executable,
        "-c",
        "import os; print(os.environ['DYLD_FALLBACK_LIBRARY_PATH'])",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{cairo}/lib:{libffi}/lib"


@pytest.mark.parametrize("missing", ["brew", "cairo", "libffi", "library_directory"])
def test_missing_prerequisites_fail_before_command(
    tmp_path: Path, missing: str
) -> None:
    environment = host(tmp_path)
    if missing != "brew":
        cairo, _ = brew(tmp_path)
        if missing == "library_directory":
            (cairo / "lib").rmdir()
        else:
            script = tmp_path / "brew"
            command(
                script,
                f'[ "$2" = "{missing}" ] && exit 1\n'
                + script.read_text().removeprefix("#!/bin/sh\n"),
            )
    result = run(environment, sys.executable, "-c", "raise RuntimeError('Command ran')")
    assert result.returncode == 1
    assert "Native Cairo dependencies" in result.stderr
    assert "brew install cairo libffi" in result.stderr
    assert "Command ran" not in result.stderr


@pytest.mark.parametrize("platform", ["Linux", "FreeBSD"])
def test_other_platforms_do_not_use_brew_or_dyld(tmp_path: Path, platform: str) -> None:
    environment = host(tmp_path, platform)
    command(tmp_path / "brew", "echo 'Unexpected brew call' >&2; exit 99\n")
    result = run(
        environment,
        sys.executable,
        "-c",
        "import os,sys; print(sys.argv[1]); print(os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', 'unset'))",
        "argument with spaces",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "argument with spaces\nunset\n"
    assert not result.stderr


def test_command_exit_status_and_usage(tmp_path: Path) -> None:
    environment = host(tmp_path, "Linux")
    assert run(environment, sys.executable, "-c", "raise SystemExit(7)").returncode == 7
    result = run(environment)
    assert result.returncode == 2
    assert "Usage:" in result.stderr
