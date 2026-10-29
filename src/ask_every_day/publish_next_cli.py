"""Interactive operator boundary for previewing and publishing the next post."""

import argparse
import fcntl
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from ask_every_day.canonical_renderer import (
    CanonicalRendererError,
    canonical_asset_valid,
    verify_editorial_fonts,
)
from ask_every_day.config import load_config
from ask_every_day.credential_store import KeychainCredentialStore
from ask_every_day.instagram_auth import CredentialManager, InstagramAuthClient
from ask_every_day.instagram_auth_cli import load_auth_config
from ask_every_day.logging_config import configure_logging
from ask_every_day.production_runtime import (
    DockerPublisherRequired,
    require_docker_publisher,
)
from ask_every_day.publication_service import PublicationService, PublicationState
from ask_every_day.publish_next import (
    CopyCatalog,
    PublishError,
    preview_next,
    publish_approved,
)
from ask_every_day.repository import open_repository

_LOG = logging.getLogger(__name__)


def credential_manager(path: Path) -> CredentialManager:
    config = load_auth_config(path)
    return CredentialManager(
        InstagramAuthClient(config), KeychainCredentialStore(config.app_id)
    )


@contextmanager
def publication_lock(database: Path):
    """Use a separate inode so the advisory lock cannot contend with SQLite locks."""
    canonical = database.resolve(strict=True)
    lock_path = canonical.with_name(canonical.name + ".publish.lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublishError("another_publisher_running") from None
        yield
    finally:
        os.close(descriptor)


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_factory: Callable[[Path], CredentialManager] | None = None,
    canonical_assets: bool = False,
) -> None:
    parser = argparse.ArgumentParser(
        description="Preview or approve one Instagram post"
    )
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    parser.add_argument("--auth-config", type=Path, default=Path("instagram-auth.toml"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args(argv)
    if args.log_file is not None and not args.confirm:
        parser.error("persistent logging requires explicit --confirm")
    configure_logging()
    failure = None
    result = None
    try:
        if args.confirm and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise PublishError("interactive_confirmation_required")
        if args.confirm:
            require_docker_publisher()
        fonts = verify_editorial_fonts() if canonical_assets or args.confirm else None
        factory = credential_factory or credential_manager
        config = load_config(args.config)
        config.database_path.resolve(strict=True)
        if args.log_file is not None:
            from ask_every_day.scheduler_cli import _bounded_log

            _bounded_log(args.log_file)
        catalog = CopyCatalog.load(args.data_dir)
        with open_repository(config, read_only=not args.confirm) as repository:
            # Preview needs no lock; confirmed runs hold it through durable verification.
            if not args.confirm:
                plan = preview_next(repository, config, catalog)
                if plan is None:
                    print(json.dumps({"mode": "preview", "queue": "empty"}))
                    return
                if canonical_assets and not canonical_asset_valid(
                    repository, config, plan
                ):
                    plan = replace(plan, asset_sha256=None)
                ready = False
                try:
                    if not canonical_assets:
                        factory(args.auth_config).local_credential()
                    ready = not canonical_assets
                except Exception:
                    pass
                print(
                    json.dumps(
                        {
                            "mode": "preview",
                            "canonical_renderer": canonical_assets,
                            "fonts": fonts,
                            "post_id": plan.post.id,
                            "queue_position": plan.entry.position,
                            "account": plan.account,
                            "prompt": plan.post.prompt,
                            "asset_path": str(plan.asset_path),
                            "background_color": plan.background_color,
                            "palette_sequence": plan.palette_sequence,
                            "asset_action": "ready"
                            if plan.asset_sha256
                            else "run_container_prepare_next",
                            "asset_valid": plan.asset_sha256 is not None,
                            "asset_sha256": plan.asset_sha256,
                            "caption": plan.caption,
                            "hashtags": plan.hashtags,
                            "credential_ready": ready,
                            "quota": "not_checked",
                            "confirmation": plan.challenge,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            with publication_lock(config.database_path):
                plan = preview_next(repository, config, catalog)
                if plan is None:
                    print(json.dumps({"mode": "confirmed", "queue": "empty"}))
                    return
                if plan.asset_sha256 is None or not canonical_asset_valid(
                    repository, config, plan
                ):
                    raise PublishError("invalid_asset")
                manager = factory(args.auth_config)
                manager.local_credential()
                print(
                    f"Account: {plan.account}\nPost: {plan.post.id} (position {plan.entry.position})"
                )
                print(
                    f"Prompt: {plan.post.prompt}\nJPEG: {plan.asset_path}\nSHA256: {plan.asset_sha256}"
                )
                print(
                    f"Background: {plan.background_color} (palette publication {plan.palette_sequence})"
                )
                print(f"Complete caption:\n{plan.caption}\n")
                print(
                    "This publishes one publicly visible Instagram post. Closing staging does not delete it."
                )
                print(
                    "Inspect the JPEG above, then type exactly: " + plan.challenge,
                    flush=True,
                )
                answer = input("> ")

                def guard(_attempt_id=None):
                    if verify_editorial_fonts() != fonts or not canonical_asset_valid(
                        repository, config, plan
                    ):
                        raise PublishError("canonical_renderer_or_asset_changed")

                if answer == plan.challenge:
                    guard()
                result = publish_approved(
                    repository,
                    config,
                    catalog,
                    plan,
                    answer,
                    manager,
                    dispatch_guard=guard,
                )
                with open_repository(config) as reopened:
                    stored = reopened.get_publication(result.attempt_id)
                    if (
                        stored is None
                        or stored.publication.external_id != result.media_id
                        or PublicationService(reopened).state(result.post_id)
                        is not PublicationState.PUBLISHED
                    ):
                        raise PublishError(
                            "durable_result_unverified",
                            attempt_id=result.attempt_id,
                            container_id=result.container_id,
                            media_id=result.media_id,
                        )
        print(
            json.dumps({"state": "Published", "staging": "closed", **result.__dict__})
        )
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        failure = (
            error
            if isinstance(error, PublishError)
            else PublishError(
                "interrupted"
                if isinstance(error, (KeyboardInterrupt, EOFError))
                else "post_publication_verification_failed"
                if result
                else str(error)
                if isinstance(error, (CanonicalRendererError, DockerPublisherRequired))
                else "local_preflight_failed",
                attempt_id=result.attempt_id if result else None,
                container_id=result.container_id if result else None,
                media_id=result.media_id if result else None,
            )
        )
    if failure is not None:
        _LOG.error(
            "publish_next_command_failed",
            extra={
                "event": "publish_next_command_failed",
                "operation": failure.reason,
                "error_type": "PublishError",
                "publication_id": failure.attempt_id,
                "container_id": failure.container_id,
                "media_id": failure.media_id,
            },
        )
        print(
            json.dumps(
                {
                    "error": failure.reason,
                    "attempt_id": failure.attempt_id,
                    "container_id": failure.container_id,
                    "media_id": failure.media_id,
                    "action": "stop_and_reconcile_do_not_retry"
                    if failure.attempt_id
                    else "fix_preflight",
                }
            ),
            file=sys.stderr,
        )
        parser.exit(1, "See docs/publish-next.md.\n")


if __name__ == "__main__":
    main()
