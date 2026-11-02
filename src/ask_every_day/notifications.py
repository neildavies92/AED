"""Best-effort terminal notifications, independent of publication authority."""

import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ask_every_day.discord_webhook import validate_url
from ask_every_day.file_credential_store import FileSecretBackend

_LOG = logging.getLogger(__name__)
_SERVICE = "ask-every-day.discord-webhook"
LONDON = ZoneInfo("Europe/London")
Callback = Callable[[dict], None]
EVENTS = {
    "publication_succeeded",
    "publication_failed",
    "publication_reconciliation_required",
    "notification_test",
}
CATEGORIES = {
    "authentication",
    "configuration",
    "transient",
    "api_rejected",
    "local_infrastructure",
}
ACTIONS = {
    "publication_succeeded": "Completed checkpoint confirmed. No retry required.",
    "publication_failed": "Inspect local JSONL logs and publication-status. Fix the cause; do not replay automatically.",
    "publication_reconciliation_required": "STOP. Inspect publication-status and reconcile the known attempt using the recovery runbook. Never retry a media POST blindly.",
    "notification_test": "Explicit delivery test only. No Instagram request or publication state access.",
}


def positive(value: object) -> int | None:
    return value if type(value) is int and 0 < value <= 2**63 - 1 else None


def post_id(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"AED-[0-9]{4}", value)
        else None
    )


@dataclass(frozen=True)
class Notice:
    event: str
    timestamp: datetime
    correlation_id: str
    post_id: str | None = None
    attempt_id: int | None = None
    checkpoint: str = "unknown"
    category: str = "local_infrastructure"
    http_status: int | None = None
    meta_error_code: int | None = None
    attempt_created: str = "unknown"
    dedup_key: str = field(default_factory=lambda: uuid.uuid4().hex, repr=False)

    def payload(self) -> dict:
        if self.event not in EVENTS or self.timestamp.utcoffset() is None:
            raise ValueError("notification_invalid")
        # Only schema-constrained values reach the embed. Never truncate arbitrary text.
        checkpoint = (
            self.checkpoint
            if self.checkpoint
            in {
                "prepared",
                "create_intent",
                "container_known",
                "publish_intent",
                "completed",
                "closed",
                "legacy_uncheckpointed",
                "unknown",
            }
            else "unknown"
        )
        category = (
            self.category if self.category in CATEGORIES else "local_infrastructure"
        )
        correlation = (
            self.correlation_id
            if re.fullmatch(r"[0-9a-f]{32}", self.correlation_id)
            else "unknown"
        )
        fields = {
            "Account": "@ask.every.day",
            "London time": self.timestamp.astimezone(LONDON).isoformat(),
            "Execution": correlation,
            "Post": post_id(self.post_id) or "unknown",
            "Attempt": str(positive(self.attempt_id) or "unknown"),
            "Attempt created": self.attempt_created
            if self.attempt_created in {"yes", "no", "unknown"}
            else "unknown",
            "Checkpoint": checkpoint,
            "Classification": "durably_confirmed"
            if self.event == "publication_succeeded"
            else category,
        }
        if type(self.http_status) is int and 100 <= self.http_status <= 599:
            fields["HTTP status"] = str(self.http_status)
        if type(self.meta_error_code) is int and 0 <= self.meta_error_code <= 2**31 - 1:
            fields["Meta error code"] = str(self.meta_error_code)
        action = ACTIONS[self.event]
        if self.event == "publication_failed" and category == "authentication":
            action += (
                " Verify account/token access; owner reauthorization may be required."
            )
        elif self.event == "publication_failed" and category == "configuration":
            action += " Review permissions and configuration."
        return {
            "allowed_mentions": {
                "parse": [],
                "users": [],
                "roles": [],
                "replied_user": False,
            },
            "embeds": [
                {
                    "title": self.event,
                    "description": action,
                    "color": 0x2F608C
                    if self.event == "publication_succeeded"
                    else 0xB22222,
                    "fields": [
                        {"name": k, "value": v, "inline": True}
                        for k, v in fields.items()
                    ],
                }
            ],
        }


class NotificationStore:
    """Dedicated files within the existing private credential mount; no token reads."""

    def __init__(self, directory: Path):
        self.backend = FileSecretBackend(directory)

    def configure(self, url: str | None) -> None:
        data: dict[str, object] = {"enabled": url is not None}
        if url is not None:
            data["url"] = validate_url(url)
        self.backend.write(_SERVICE, "credential", json.dumps(data).encode())

    def destination(self) -> str | None:
        raw = self.backend.read(_SERVICE, "credential")
        if raw is None:
            return None
        value = json.loads(raw)
        if value == {"enabled": False}:
            return None
        if set(value) != {"enabled", "url"} or value["enabled"] is not True:
            raise ValueError("notification_configuration")
        return validate_url(value["url"])

    @staticmethod
    def service(key: str) -> str:
        return _SERVICE + ".receipt." + hashlib.sha256(key.encode()).hexdigest()

    def reserve(self, key: str) -> bool:
        # Serialize only receipt admission, not network delivery or publishing.
        def operation(directory: int) -> bool:
            fd = os.open(
                "discord-notifications.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            try:
                self.backend._private(os.fstat(fd))
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                service = self.service(key)
                if self.backend.read(service, "credential") is not None:
                    return False
                self.backend.write(
                    service, "credential", b'{"delivery_result":"admitted"}'
                )
                return True
            finally:
                os.close(fd)

        return self.backend._operation(operation)

    def complete(self, key: str, result: dict) -> None:
        self.backend.write(self.service(key), "credential", json.dumps(result).encode())


def discord_send(url: str, payload: dict) -> dict:
    """A disposable worker bounds DNS/TLS too; only that worker may be terminated."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ask_every_day.discord_webhook"],
            input=json.dumps({"url": url, "payload": payload}).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=6,
            check=False,
        )
        value = json.loads(result.stdout) if result.returncode == 0 else {}
        if value.get("delivery_result") not in {
            "delivered",
            "rejected",
            "rate_limited",
            "uncertain",
            "configuration_error",
        }:
            raise ValueError
        status = value.get("http_status")
        return {
            "delivery_result": value["delivery_result"],
            "http_status": status
            if type(status) is int and 100 <= status <= 599
            else None,
        }
    except Exception:
        return {"delivery_result": "uncertain", "http_status": None}


def send_notice(
    store: NotificationStore, notice: Notice, *, sender=discord_send
) -> dict:
    start = time.monotonic()
    result = {"delivery_result": "configuration_error", "http_status": None}
    try:
        url = store.destination()
        if url is None:
            result = {"delivery_result": "disabled", "http_status": None}
        else:
            payload = notice.payload()
            if len(json.dumps(payload).encode()) > 4096:
                raise ValueError
            if not store.reserve(notice.dedup_key):
                result = {
                    "delivery_result": "duplicate_suppressed",
                    "http_status": None,
                }
            else:
                result = {"delivery_result": "uncertain", "http_status": None}
                try:
                    result = sender(url, payload)
                except Exception:
                    pass
                store.complete(notice.dedup_key, result)
    except Exception:
        # Storage or network failures never escape into a publication result.
        pass
    _LOG.info(
        "notification_delivery",
        extra={
            "event": "notification_delivery",
            "notification_event": notice.event if notice.event in EVENTS else "invalid",
            "correlation_id": notice.correlation_id
            if re.fullmatch(r"[0-9a-f]{32}", notice.correlation_id)
            else "unknown",
            "post_id": post_id(notice.post_id),
            "publication_id": positive(notice.attempt_id),
            **result,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        },
    )
    return result


def emit(callback: Callback | None, outcome: dict) -> None:
    if callback is not None:
        try:
            callback(outcome)
        except BaseException:
            _LOG.warning(
                "notification_callback_failed",
                extra={
                    "event": "notification_callback_failed",
                    "failure_category": "local_infrastructure",
                },
            )


class PublicationNotifier:
    def __init__(
        self, config: Path, directory: Path, source: str, *, sender=discord_send
    ):
        self.config = config
        self.store = NotificationStore(directory)
        self.source = source
        self.correlation = uuid.uuid4().hex
        self.sender = sender

    def __call__(self, outcome: dict) -> None:
        # Disabled/missing configuration neither opens SQLite nor contacts Discord.
        if self.store.destination() is None:
            return
        notice = self.describe(outcome)
        send_notice(self.store, notice, sender=self.sender)

    def describe(self, outcome: dict) -> Notice:
        from ask_every_day.config import load_config
        from ask_every_day.publication_recovery import snapshot
        from ask_every_day.repository import open_repository

        stamp = datetime.now(UTC)
        attempt = positive(outcome.get("attempt_id"))
        post = post_id(outcome.get("post_id"))
        checkpoint = "unknown"
        provider_terminal = False
        created = (
            "yes"
            if attempt
            else "no"
            if outcome.get("publication_state") == "no_attempt"
            else "unknown"
        )
        event = (
            "publication_failed"
            if created == "no"
            else "publication_reconciliation_required"
        )
        identity = (
            f"{self.source}:{stamp.astimezone(LONDON).date()}"
            if self.source == "scheduled"
            else self.correlation
        )
        if attempt:
            try:
                with open_repository(load_config(self.config), read_only=True) as repo:
                    state = snapshot(repo, attempt)
                    report = state.report()
                    post = state.record.publication.post_id
                    checkpoint = str(report["checkpoint"])
                    provider_terminal = report["resolution"] in {
                        "container_error",
                        "container_expired",
                    }
                    identity = f"{attempt}:{post}:{state.record.publication.attempted_at.isoformat()}"
                    if (
                        report["definitely_completed"]
                        and report["media_known"]
                        and checkpoint == "completed"
                    ):
                        event = "publication_succeeded"
                        stamp = state.record.publication.published_at or stamp
                    elif (
                        checkpoint == "closed"
                        and not report["remote_outcome_uncertain"]
                    ):
                        event = "publication_failed"
            except Exception:
                pass
        if outcome.get("outcome") == "published" and event != "publication_succeeded":
            event = "publication_reconciliation_required"
        diagnostics = outcome.get("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        category = diagnostics.get(
            "failure_category",
            "api_rejected" if provider_terminal else "local_infrastructure",
        )
        category = category if isinstance(category, str) else "local_infrastructure"
        return Notice(
            event,
            stamp,
            self.correlation,
            post,
            attempt,
            checkpoint,
            category if category in CATEGORIES else "local_infrastructure",
            diagnostics.get("http_status"),
            diagnostics.get("meta_error_code"),
            created,
            f"{event}:{identity}",
        )
