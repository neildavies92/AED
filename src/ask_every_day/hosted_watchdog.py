"""Read durable publication evidence and report a missed hosted slot."""

import argparse
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.domain import PublicationStatus
from ask_every_day.logging_config import configure_logging
from ask_every_day.notifications import (
    Notice,
    NotificationStore,
    PublicationNotifier,
    positive,
    post_id,
    send_notice,
)
from ask_every_day.publication_recovery import snapshot
from ask_every_day.repository import open_repository
from ask_every_day.scheduler_policy import LONDON, load_schedule


def observe(config_path: Path, credential_dir: Path, day: date, admission: str) -> dict:
    schedule = load_schedule(Path("/etc/aed/schedule.toml"))
    start = datetime(
        day.year, day.month, day.day, schedule.hour, schedule.minute, tzinfo=LONDON
    )
    if not schedule.in_window(start):
        return {"classification": "not_due", "alert_delivered": False}
    config = load_config(config_path)
    reports: list[dict[str, object]] = []
    completed: list[dict[str, object]] = []
    with open_repository(config, read_only=True) as repo:
        for row in repo.list_publications():
            publication = row.publication
            published_today = (
                publication.published_at is not None
                and publication.published_at.astimezone(LONDON).date() == day
            )
            if (
                publication.attempted_at.astimezone(LONDON).date() == day
                or published_today
                or publication.status is PublicationStatus.PUBLISHING
            ):
                report = snapshot(repo, row.id).report()
                reports.append(report)
                if (
                    published_today
                    and report["definitely_completed"]
                    and report["media_known"]
                    and report["checkpoint"] == "completed"
                ):
                    completed.append(report)

    store = NotificationStore(credential_dir)
    report = completed[0] if len(completed) == 1 else reports[-1] if reports else {}
    if len(completed) > 1 or any(r["operator_action_required"] for r in reports):
        classification = "outcome_uncertain"
    elif len(completed) == 1:
        notice = PublicationNotifier(config_path, credential_dir, "scheduled").describe(
            {"outcome": "published", "attempt_id": report["attempt_id"]}
        )
        classification = (
            "completed" if store.delivered(notice.dedup_key) else "notification_failed"
        )
    elif (
        reports
        and report.get("checkpoint") == "closed"
        and not report["remote_outcome_uncertain"]
    ):
        classification = "publishing_failed"
    elif reports or admission in {"admitted", "outcome_unverified", "published"}:
        classification = "outcome_uncertain"
    else:
        classification = {
            "missing": "scheduler_never_triggered",
            "preflight_failed": "preflight_failed",
            "infrastructure_unavailable": "infrastructure_unavailable",
        }.get(admission, "publication_missing")
    if classification == "completed":
        return {"classification": classification, "alert_delivered": False}
    notice = Notice(
        "scheduled_watchdog",
        datetime.now(UTC),
        uuid.uuid4().hex,
        post_id=post_id(report.get("post_id")),
        attempt_id=positive(report.get("attempt_id")),
        checkpoint=str(report.get("checkpoint", "unknown")),
        category=classification,
        dedup_key=f"hosted-watchdog:{day}:{classification}",
    )
    send_notice(store, notice)
    return {
        "classification": classification,
        "alert_delivered": store.delivered(notice.dedup_key),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day", type=date.fromisoformat)
    parser.add_argument("admission")
    args = parser.parse_args()
    configure_logging()
    try:
        print(
            json.dumps(
                observe(
                    Path("/etc/aed/aed.toml"),
                    Path("/var/lib/aed-credentials"),
                    args.day,
                    args.admission,
                )
            )
        )
    except Exception:
        raise SystemExit("hosted_watchdog_unavailable") from None


if __name__ == "__main__":
    main()
