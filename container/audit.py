"""Probe excluded credential/state paths using a disposable synthetic build context."""

import json
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    marker = ("SYNTHETIC_EXCLUDED_" + secrets.token_hex(32)).encode()
    image = "aed053-context-audit:" + secrets.token_hex(8)
    with tempfile.TemporaryDirectory(prefix="aed053-context-") as temporary:
        directory = Path(temporary)
        context = directory / "context"
        context.mkdir()
        for name in (
            "Dockerfile",
            ".dockerignore",
            "pyproject.toml",
            "uv.lock",
            "README.md",
        ):
            shutil.copyfile(ROOT / name, context / name)
        for name in ("src", "data", "tests", "scripts", "container", "deployment"):
            shutil.copytree(
                ROOT / name,
                context / name,
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".venv", "output", "config", "state", "credentials"
                ),
            )
        for name in (
            ".env",
            "aed.toml",
            "instagram-auth.toml",
            "data/aed.sqlite3",
            "data/aed.sqlite3.palette.json",
            "secrets/token",
            "deployment/credentials/token",
            "deployment/state/aed.sqlite3",
            "assets/generated/private.jpg",
        ):
            path = context / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(marker)
        try:
            build = subprocess.run(
                ["docker", "build", "--target", "runtime", "-t", image, str(context)],
                capture_output=True,
                check=True,
            )
            assert marker not in build.stdout + build.stderr
            metadata = subprocess.check_output(["docker", "image", "inspect", image])
            history = subprocess.check_output(
                ["docker", "history", "--no-trunc", image]
            )
            assert marker not in metadata + history
            archive = directory / "image.tar"
            subprocess.run(
                ["docker", "image", "save", "-o", str(archive), image], check=True
            )
            files_checked = 0
            with tarfile.open(archive) as outer:
                manifest_stream = outer.extractfile("manifest.json")
                assert manifest_stream is not None
                manifests = json.load(manifest_stream)
                layer_names = {
                    name for manifest in manifests for name in manifest["Layers"]
                }
                for name in sorted(layer_names):
                    layer_stream = outer.extractfile(name)
                    assert layer_stream is not None
                    with tarfile.open(fileobj=layer_stream, mode="r|*") as layer:
                        for member in layer:
                            if not member.isfile():
                                continue
                            stream = layer.extractfile(member)
                            assert stream is not None
                            previous = b""
                            while payload := stream.read(1024 * 1024):
                                assert marker not in previous + payload
                                previous = payload[-len(marker) :]
                            files_checked += 1
            assert files_checked > 100
            print(
                json.dumps(
                    {
                        "excluded_synthetic_secrets_absent_from_layers_history_logs": True,
                        "layer_files_checked": files_checked,
                        "production_credentials_accessed": False,
                    }
                )
            )
        finally:
            subprocess.run(
                ["docker", "image", "rm", image], check=False, capture_output=True
            )


if __name__ == "__main__":
    main()
