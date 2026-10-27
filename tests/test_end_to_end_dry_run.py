"""Real local components compose; only the remote publication result is simulated."""

import http.client
import socket
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from ask_every_day.batch_render import BatchRenderResult, render_assets
from ask_every_day.content_import import import_snapshot
from ask_every_day.domain import AppConfig, PublicationStatus
from ask_every_day.publication_service import (
    InvalidTransition,
    PublicationService,
    PublicationState,
)
from ask_every_day.queue_rules import validate_sequence
from ask_every_day.queue_service import generate_and_store_queue
from ask_every_day.repository import ImportResult, open_repository

DATA = Path(__file__).parents[1] / "data"
ATTEMPTED = datetime(2026, 10, 17, 12, 0, 0, 123456, tzinfo=UTC)
PUBLISHED = datetime(2026, 10, 17, 12, 0, 5, 654321, tzinfo=UTC)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("The local dry run must never access the network")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, denied)
    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(http.client.HTTPConnection, "request", denied)


def settings(tmp_path: Path) -> AppConfig:
    return AppConfig(
        database_path=tmp_path / "runtime.sqlite3",
        assets_path=tmp_path / "assets",
        account_handle="@local.dry.run",
    )


def test_snapshot_to_published_with_representative_batch(tmp_path: Path) -> None:
    started = time.monotonic()
    snapshots = [
        DATA / name for name in ("prompts.csv", "taxonomy.csv", "hashtags.csv")
    ]
    original_bytes = [path.read_bytes() for path in snapshots]
    config = settings(tmp_path)
    with open_repository(config) as repo:
        assert import_snapshot(repo, *snapshots) == ImportResult(400, 0)
        posts = repo.list_posts()
        assert len(posts) == 400
        assert all(
            p.caption_override is None and p.hashtag_set_id is None for p in posts
        )
        service = PublicationService(repo)
        assert service.state("AED-0395") is PublicationState.READY
        queue = generate_and_store_queue(repo, seed=42)
        assert [entry.position for entry in queue] == list(range(1, 401))
        assert len({entry.post_id for entry in queue}) == 400
        by_id = {post.id: post for post in posts}
        assert {entry.post_id for entry in queue} == set(by_id)
        validate_sequence([by_id[entry.post_id] for entry in queue])
        assert queue[0].post_id == "AED-0395"
        selected = by_id[queue[0].post_id]
    # user_version is the public SQLite schema version, not a private table layout.
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3

    with open_repository(config) as repo:
        assert repo.list_queue() == queue
        assert repo.next_queue_entry() == queue[0]
        assert repo.get_post(selected.id) == selected
        assert repo.publication_history(selected.id) == []
        assert PublicationService(repo).state(selected.id) is PublicationState.QUEUED

    # Batch rendering consumes every stored Post. Persist an exact representative
    # copy in a second real database rather than mock/filter that application API.
    render_config = replace(config, database_path=tmp_path / "representative.sqlite3")
    with open_repository(render_config) as render_repo:
        assert render_repo.import_posts([selected]) == ImportResult(1, 0)
    with open_repository(render_config) as render_repo:
        assert render_repo.list_posts() == [selected]
        assert render_repo.get_post(selected.id) == selected
        assert render_assets(render_repo, render_config) == BatchRenderResult(1, 1, 1)
    for suffix, expected_format in (("png", "PNG"), ("jpg", "JPEG")):
        with Image.open(config.assets_path / f"{selected.id}.{suffix}") as image:
            image.load()
            assert image.format == expected_format
            assert image.mode == "RGB"
            assert image.size == (config.renderer.width, config.renderer.height)

    with open_repository(config) as repo:
        service = PublicationService(repo)
        attempt = service.begin_publish(selected.id, ATTEMPTED)
        assert service.state(selected.id) is PublicationState.PUBLISHING
        assert repo.publication_history(selected.id) == [attempt]
        assert attempt.publication.status is PublicationStatus.PUBLISHING
        assert attempt.publication.attempted_at == ATTEMPTED
        assert attempt.publication.post_id == selected.id
        assert repo.list_queue() == queue[1:]
    with open_repository(config) as repo:
        service = PublicationService(repo)
        assert service.state(selected.id) is PublicationState.PUBLISHING
        service.record_success(attempt.id, PUBLISHED, external_id="dry-run-media-001")
    with open_repository(config) as repo:
        assert repo.get_post(selected.id) == selected
        assert len(repo.list_posts()) == 400
        assert PublicationService(repo).state(selected.id) is PublicationState.PUBLISHED
        assert repo.publication_history(selected.id) == [
            replace(
                attempt,
                publication=replace(
                    attempt.publication,
                    status=PublicationStatus.PUBLISHED,
                    published_at=PUBLISHED,
                    external_id="dry-run-media-001",
                ),
            )
        ]
        assert repo.list_queue() == queue[1:]
        assert repo.next_queue_entry() == queue[1]
    assert [path.read_bytes() for path in snapshots] == original_bytes
    print(f"Local end-to-end dry run: {time.monotonic() - started:.2f}s")


def test_failed_result_is_persisted_and_terminal(tmp_path: Path) -> None:
    config = settings(tmp_path)
    with open_repository(config) as repo:
        import_snapshot(
            repo, DATA / "prompts.csv", DATA / "taxonomy.csv", DATA / "hashtags.csv"
        )
        queue = generate_and_store_queue(repo, seed=42)
        service = PublicationService(repo)
        attempt = service.begin_publish(queue[0].post_id, ATTEMPTED)
        service.record_failure(attempt.id, error="synthetic-local-failure")
    with open_repository(config) as repo:
        service = PublicationService(repo)
        assert service.state(queue[0].post_id) is PublicationState.FAILED
        history = repo.publication_history(queue[0].post_id)
        assert history == [
            replace(
                attempt,
                publication=replace(
                    attempt.publication,
                    status=PublicationStatus.FAILED,
                    error="synthetic-local-failure",
                ),
            )
        ]
        assert history[0].publication.published_at is None
        assert history[0].publication.external_id is None
        with pytest.raises(InvalidTransition):
            service.begin_publish(queue[0].post_id, PUBLISHED)
        assert repo.publication_history(queue[0].post_id) == history
        assert repo.list_queue() == queue[1:]
