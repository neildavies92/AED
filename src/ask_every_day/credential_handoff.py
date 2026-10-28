"""Explicit interactive owner export to private files; never an automatic hook."""

import argparse
import json
import sys
from pathlib import Path

from ask_every_day.file_credential_store import FileCredentialStore
from ask_every_day.instagram_auth import AuthError
from ask_every_day.instagram_auth_cli import load_auth_config
from ask_every_day.publish_next_cli import credential_manager

CHALLENGE = "EXPORT INSTAGRAM CREDENTIAL @ask.every.day"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-config", type=Path, default=Path("instagram-auth.toml"))
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise AuthError("interactive_input_required")
        config = load_auth_config(args.auth_config)
        if args.destination.exists() or args.destination.is_symlink():
            raise AuthError("new_private_destination_required")
        print("This creates a plaintext 0600 credential in a new 0700 directory.")
        print(
            "Use only protected encrypted storage and an owner-approved SSH transfer."
        )
        print("No App Secret is exported. No provider request or publication is made.")
        print("Type exactly: " + CHALLENGE)
        if input("> ") != CHALLENGE:
            raise AuthError("export_confirmation_required")
        # Construct/access Keychain only after exact explicit operator approval.
        credential = credential_manager(args.auth_config).local_credential()
        args.destination.mkdir(mode=0o700)
        store = FileCredentialStore(config.app_id, args.destination)
        store.save(credential)
        if store.load() != credential:
            raise AuthError("export_verification")
        print(json.dumps({"credential_exported": True, "account": "@ask.every.day"}))
    except (Exception, KeyboardInterrupt):
        parser.exit(
            1, "Credential handoff failed; inspect private destination locally.\n"
        )


if __name__ == "__main__":
    main()
