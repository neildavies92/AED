"""Local status and exact, operator-confirmed reconciliation; never publish media."""

import argparse
import json
import logging
import sys
from contextlib import nullcontext
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.instagram_client import ContainerStatus, InstagramClient
from ask_every_day.logging_config import configure_logging
from ask_every_day.publication_recovery import (
    RecoveryError,
    apply_recovery,
    backup_state,
    load_response_evidence,
    plan_recovery,
    remote_inspection,
    snapshot,
)
from ask_every_day.publish_next_cli import credential_manager, publication_lock
from ask_every_day.repository import open_repository

_LOG = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly reconcile one publication"
    )
    parser.add_argument("command", choices=("status", "reconcile", "backup", "migrate"))
    parser.add_argument("--attempt-id", type=int)
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    parser.add_argument("--auth-config", type=Path, default=Path("instagram-auth.toml"))
    parser.add_argument(
        "--remote", action="store_true", help="Allow identity/container/media GETs only"
    )
    parser.add_argument("--resolve", choices=("published", "failed"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--show-ids", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=Path("output/state-backups"))
    args = parser.parse_args()
    configure_logging()
    failure = None
    try:
        if args.confirm and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise RecoveryError("interactive_recovery_confirmation_required")
        if args.command in ("status", "backup", "migrate") and (
            args.remote or args.resolve or args.confirm or args.evidence
        ):
            raise RecoveryError("status_is_local_read_only")
        if args.command == "reconcile" and (
            args.attempt_id is None or args.attempt_id < 1
        ):
            raise RecoveryError("exact_attempt_required")
        if args.confirm and (not args.remote or not args.resolve):
            raise RecoveryError("explicit_remote_resolution_required")
        if args.resolve and not args.remote:
            raise RecoveryError("remote_evidence_required")
        if args.resolve == "published" and args.evidence is None:
            raise RecoveryError("captured_response_evidence_required")
        if args.evidence is not None and args.resolve != "published":
            raise RecoveryError("evidence_requires_published_resolution")
        config = load_config(args.config)
        if config.account_handle != "@ask.every.day":
            raise RecoveryError("account_configuration")
        if args.command in ("backup", "migrate"):
            with publication_lock(config.database_path):
                saved = backup_state(config, args.backup_dir)
                if args.command == "migrate":
                    with open_repository(config):
                        pass
            print(
                json.dumps(
                    {
                        "mode": args.command,
                        "backup": str(saved),
                        "schema_migrated": args.command == "migrate",
                    }
                )
            )
            return
        lock = publication_lock(config.database_path) if args.confirm else nullcontext()
        with lock:
            with open_repository(config, read_only=True) as repository:
                if args.command == "status":
                    ids = (
                        [args.attempt_id]
                        if args.attempt_id is not None
                        else [r.id for r in repository.list_publications()]
                    )
                    print(
                        json.dumps(
                            {
                                "mode": "local_read_only",
                                "attempts": [
                                    snapshot(repository, number).report(
                                        show_ids=args.show_ids
                                    )
                                    for number in ids
                                ],
                            },
                            indent=2,
                        )
                    )
                    return
                current = snapshot(repository, args.attempt_id)
                output = current.report(show_ids=args.show_ids)
                output["mode"] = (
                    "remote_read_only" if args.remote else "local_read_only"
                )
                if not args.remote:
                    print(json.dumps(output, indent=2))
                    return
                response = (
                    load_response_evidence(args.evidence) if args.evidence else None
                )
                manager = credential_manager(args.auth_config)
                remote = remote_inspection(
                    current,
                    manager,
                    media_id=response.media_id if response else None,
                    client_factory=InstagramClient,
                )
                output.update(
                    remote_publication_observed=remote.container_status
                    is ContainerStatus.PUBLISHED,
                    remote_outcome_uncertain=(
                        output["remote_outcome_uncertain"]
                        and remote.container_status is not ContainerStatus.PUBLISHED
                    ),
                    published_media_identity_unresolved=(
                        remote.container_status is ContainerStatus.PUBLISHED
                        and current.record.publication.external_id is None
                    ),
                    remote_container_status=remote.container_status.value
                    if remote.container_status
                    else None,
                    media_owner_verified=remote.media is not None,
                    mapping_limitation="container_status_does_not_identify_published_media",
                )
                plan = (
                    plan_recovery(current, remote, args.resolve, response)
                    if args.resolve
                    else None
                )
                if plan is not None:
                    output.update(
                        proposed_local_state=plan.action, confirmation=plan.challenge
                    )
                if not args.confirm:
                    print(json.dumps(output, indent=2))
                    return
                assert plan is not None
                print(
                    json.dumps(
                        {
                            **output,
                            **current.report(show_ids=True),
                            "proposed_media_id": response.media_id
                            if response
                            else None,
                            "remote_media_timestamp": remote.media.published_at.isoformat()
                            if remote.media
                            else None,
                        },
                        indent=2,
                    )
                )
                if plan.action == "published":
                    print(
                        "Attest that the independently captured media_publish response maps this exact attempt/container to this media ID. Caption, time and image similarity are insufficient."
                    )
                else:
                    print(
                        "Close only this attempt as terminal Failed. It will not be requeued or retried."
                    )
                print(
                    "No Instagram POST will be sent. Type exactly: " + plan.challenge,
                    flush=True,
                )
                answer = input("> ")
                if answer != plan.challenge:
                    raise RecoveryError("recovery_confirmation_required")
            with open_repository(config) as writable:
                apply_recovery(
                    writable,
                    config,
                    plan,
                    answer,
                    manager,
                    client_factory=InstagramClient,
                )
            with open_repository(config, read_only=True) as reopened:
                result = snapshot(reopened, args.attempt_id)
                expected = "Published" if args.resolve == "published" else "Failed"
                if result.record.publication.status.value != expected:
                    raise RecoveryError("recovery_result_unverified")
                print(
                    json.dumps(
                        {
                            "mode": "local_recovery_completed",
                            **result.report(show_ids=args.show_ids),
                        }
                    )
                )
    except (KeyboardInterrupt, EOFError):
        failure = "recovery_cancelled"
    except Exception as error:
        failure = (
            str(error)
            if isinstance(error, RecoveryError)
            else "recovery_operation_failed"
        )
    if failure:
        _LOG.error(
            "publication_recovery_failed",
            extra={
                "event": "publication_recovery_failed",
                "error_type": "RecoveryError",
                "operation": failure,
            },
        )
        parser.exit(
            1, failure + "; no publishing retry; see docs/publication-recovery.md.\n"
        )


if __name__ == "__main__":
    main()
