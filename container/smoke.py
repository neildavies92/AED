"""Run disposable, network-isolated Linux acceptance; never mount production state."""

import argparse
import json
import subprocess
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="ask-every-day:local")
    parser.add_argument("--output", type=Path, default=Path("output/aed-053-review"))
    args = parser.parse_args()
    volume = "aed053-test-" + uuid.uuid4().hex
    script = Path(__file__).with_name("smoke_case.py").read_bytes()

    def docker(*arguments, payload=None):
        result = subprocess.run(
            ["docker", *arguments], input=payload, capture_output=True, check=True
        )
        return result.stdout

    docker("volume", "create", volume)
    mount = ["--mount", f"type=volume,src={volume},dst=/var/lib/aed"]
    restricted = [
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        *mount,
    ]
    try:
        docker(
            *restricted,
            "--user",
            "0:0",
            "--entrypoint",
            "chown",
            args.image,
            "10001:10001",
            "/var/lib/aed",
        )
        result = docker(
            *restricted,
            "-i",
            "--entrypoint",
            "python",
            args.image,
            "-",
            "initialize",
            payload=script,
        )
        print(result.decode().strip())
        result = docker(
            *restricted, args.image, "preview", "--config", "/var/lib/aed/aed.toml"
        )
        assert json.loads(result)["mode"] == "local_read_only"
        canonical = json.loads(
            docker(
                *restricted,
                args.image,
                "manual-preview",
                "--config",
                "/var/lib/aed/aed.toml",
            )
        )
        assert canonical["canonical_renderer"] and canonical["asset_valid"]
        assert canonical["caption"] and len(canonical["fonts"]) == 8
        assert canonical["asset_sha256"] and not canonical["credential_ready"]
        # A separate container proves preview left history, palette and files intact.
        verified = docker(
            *restricted,
            "-i",
            "--entrypoint",
            "python",
            args.image,
            "-",
            "verify",
            payload=script,
        )
        print(verified.decode().strip())
        print(json.dumps({"canonical_preview_read_only": True}))
        disabled = docker(
            *restricted,
            args.image,
            "schedule-run",
            "--config",
            "/var/lib/aed/aed.toml",
            "--log-file",
            "/var/lib/aed/scheduler.jsonl",
        )
        assert json.loads(disabled)["outcome"] == "disabled"
        retained = docker(
            *restricted,
            "--entrypoint",
            "cat",
            args.image,
            "/var/lib/aed/scheduler.jsonl",
        )
        assert b"scheduler_command_completed" in retained
        print(
            json.dumps(
                {
                    "unauthorized_scheduler_disabled": True,
                    "structured_log_survived_container_removal": True,
                }
            )
        )
        args.output.mkdir(parents=True, exist_ok=True)
        for post in ("AED-0395", "AED-0236"):
            payload = docker(
                *restricted,
                "--entrypoint",
                "cat",
                args.image,
                f"/var/lib/aed/assets/{post}.jpg",
            )
            (args.output / f"{post}-linux.jpg").write_bytes(payload)
        metadata = docker(
            *restricted,
            "--entrypoint",
            "python",
            args.image,
            "-c",
            'import json,sys,os,cairosvg;print(json.dumps({"python":sys.version.split()[0],"uid":os.geteuid(),"cairosvg":cairosvg.__version__}))',
        )
        print(metadata.decode().strip())
        fonts = {
            name: docker(
                *restricted,
                "--entrypoint",
                "fc-match",
                args.image,
                "-f",
                "%{family}\n",
                name,
            )
            .decode()
            .strip()
            for name in ("Liberation Serif", "Lato", "Times New Roman")
        }
        print(
            json.dumps(
                {"resolved_font_families": fonts, "cross_os_raster_parity": False}
            )
        )
        selected = json.loads(
            docker(
                *restricted,
                "--entrypoint",
                "python",
                args.image,
                "-m",
                "ask_every_day.font_review",
                "inventory",
            )
        )
        assert len(selected) == 8 and all(
            row["exact_family"] and row["style_matches"] for row in selected
        )
        print(json.dumps({"editorial_font_styles_verified": True}))
        print(
            json.dumps(
                {
                    "review_images": str(args.output.resolve()),
                    "network": "none",
                    "production_mounts": False,
                }
            )
        )
    finally:
        docker("volume", "rm", volume)


if __name__ == "__main__":
    main()
