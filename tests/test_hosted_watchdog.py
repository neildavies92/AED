"""The watchdog reads checkpoints and receipt evidence without a provider request."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from ask_every_day import hosted_watchdog
from ask_every_day.notifications import PublicationNotifier
from ask_every_day.publish_next import PublishError
from ask_every_day.scheduler_policy import LONDON, ScheduleConfig
from tests.test_notifications import store as store
from tests.test_publish_next import NOW
from tests.test_publish_next import run as run


@pytest.fixture
def observe(run, store, monkeypatch):
    monkeypatch.setattr(hosted_watchdog, "load_config", lambda _: run.config)
    monkeypatch.setattr("ask_every_day.config.load_config", lambda _: run.config)
    monkeypatch.setattr(hosted_watchdog, "load_schedule", lambda _: ScheduleConfig())

    def send(store, notice):
        store.complete(notice.dedup_key, {"delivery_result": "delivered"})

    monkeypatch.setattr(hosted_watchdog, "send_notice", Mock(side_effect=send))
    return lambda admission: hosted_watchdog.observe(
        Path("synthetic"),
        store.backend.directory,
        NOW.astimezone(LONDON).date(),
        admission,
    )


@pytest.mark.parametrize(
    "admission,classification",
    [
        ("missing", "scheduler_never_triggered"),
        ("preflight_failed", "preflight_failed"),
        ("infrastructure_unavailable", "infrastructure_unavailable"),
        ("admitted", "outcome_uncertain"),
        ("published", "outcome_uncertain"),
    ],
)
def test_missing_completion_uses_durable_admission(observe, admission, classification):
    assert observe(admission) == {
        "classification": classification,
        "alert_delivered": True,
    }


def test_published_but_notification_missing_is_not_a_publish_failure(
    run, store, observe
):
    result = run.publish()
    before = run.config.database_path.read_bytes()
    assert observe("published")["classification"] == "notification_failed"
    notice = PublicationNotifier(
        Path("synthetic"), store.backend.directory, "scheduled"
    ).describe({"outcome": "published", "attempt_id": result.attempt_id})
    store.complete(notice.dedup_key, {"delivery_result": "delivered"})
    assert observe("published")["classification"] == "completed"
    assert run.config.database_path.read_bytes() == before
    assert run.client.publish_container.call_count == 1


def test_uncertain_attempt_is_never_replayed(run, observe):
    run.client.publish_container.side_effect = TimeoutError("synthetic")
    with pytest.raises(PublishError):
        run.publish()
    assert observe("outcome_unverified")["classification"] == "outcome_uncertain"
    assert run.client.publish_container.call_count == 1


def test_terminal_provider_failure_is_reported_without_another_post(run, observe):
    from ask_every_day.instagram_client import ContainerStatus

    run.client.container_status.return_value = ContainerStatus.ERROR
    with pytest.raises(PublishError):
        run.publish()
    assert observe("outcome_unverified")["classification"] == "publishing_failed"
    run.client.publish_container.assert_not_called()
