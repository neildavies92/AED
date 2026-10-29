"""Orchestrator acceptance uses real SQLite, synthetic assets and no network."""

import hashlib
import json
import logging
import socket
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image

from ask_every_day.copy_policy import Caption, CaptionProfile, HashtagSet
from ask_every_day.domain import (
    AppConfig,
    Post,
    Publication,
    PublicationStatus,
    QueueEntry,
)
from ask_every_day.instagram_auth import SCOPES, AuthError, InstagramCredential
from ask_every_day.instagram_client import (
    Container,
    ContainerStatus,
    InstagramIdentity,
    PublishedMedia,
    PublishingQuota,
    ResponseError,
    TransportError,
)
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.media_staging import StagedMedia
from ask_every_day.publication_assets import prepare_next_asset
from ask_every_day.publication_service import PublicationService, PublicationState
from ask_every_day.publish_next import (
    CopyCatalog,
    PublishError,
    preview_next,
    publish_approved,
)
from ask_every_day.repository import open_repository

NOW = datetime(2026, 10, 26, 21, 0, tzinfo=UTC)
PRIVATE = "SYNTHETIC_PRIVATE_TOKEN_OR_RESPONSE"
URL = "https://example.test/SYNTHETIC_PRIVATE_CAPABILITY"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        socket,
        "create_connection",
        Mock(side_effect=AssertionError("network forbidden")),
    )


@pytest.fixture
def run(tmp_path, monkeypatch):
    # Synthetic provider acceptance explicitly models the Docker production boundary.
    monkeypatch.setattr("ask_every_day.production_runtime.docker_runtime", lambda: True)
    config = AppConfig(tmp_path / "db.sqlite", tmp_path / "assets", "@ask.every.day")
    config.assets_path.mkdir()
    image = BytesIO()
    with Image.new("RGB", (1080, 1350), "white") as im:
        im.save(image, format="JPEG")
    posts = [
        Post(f"AED-{i:04}", f"Question {i}?", "Category", "Subcategory", "Classic")
        for i in (1, 2, 3)
    ]
    catalog = CopyCatalog(
        [
            Caption("CAP-001", "Reflect."),
            Caption("CAP-002", "Take note.", cta_role="journal"),
        ],
        {
            p.id: CaptionProfile(p.id, "question", "reflect", "timeless", False)
            for p in posts
        },
        [
            HashtagSet(
                "TAG-001",
                "General",
                ("#reflection", "#journaling"),
                ("All categories",),
            )
        ],
    )
    credential = InstagramCredential(
        PRIVATE,
        "101",
        "102",
        "103",
        "ask.every.day",
        SCOPES,
        NOW - timedelta(days=1),
        NOW + timedelta(days=59),
    )
    credentials = Mock()
    credentials.get_valid_credential.return_value = credential
    credentials.local_credential.return_value = credential
    client = Mock()
    client.identify_account.return_value = InstagramIdentity(
        "103", "ask.every.day", "MEDIA_CREATOR"
    )
    client.publishing_quota.return_value = PublishingQuota(0, 100, 86400)
    client.create_image_container.return_value = Container("104")
    client.container_status.return_value = ContainerStatus.FINISHED
    client.publish_container.return_value = PublishedMedia("105")
    with open_repository(config) as repo:
        for p in posts:
            repo.create_post(p)
            (config.assets_path / f"{p.id}.jpg").write_bytes(image.getvalue())
        repo.replace_queue([QueueEntry(posts[1].id, 7), QueueEntry(posts[0].id, 9)])
        events = []

        @contextmanager
        def stage(repository, cfg, post_id, *, expected_sha256=None):
            assert cfg is config
            assert repository.list_posts() == repo.list_posts()
            assert (
                hashlib.sha256(
                    (cfg.assets_path / f"{post_id}.jpg").read_bytes()
                ).hexdigest()
                == expected_sha256
            )
            assert repo.list_queue()[0].post_id == post_id
            assert repo.publication_history(post_id) == []
            events.append("open")
            try:
                yield StagedMedia(post_id, URL)
            finally:
                events.append("closed")

        class Run:
            def __init__(self):
                self.repo = repo
                self.config = config
                self.catalog = catalog
                self.credentials = credentials
                self.client = client
                self.events = events
                self.stage = stage

            def preview(self):
                return preview_next(repo, config, catalog)

            def publish(self, **kwargs):
                plan = kwargs.pop("plan", self.preview())
                assert plan is not None
                confirmation = kwargs.pop("confirmation", plan.challenge)
                return publish_approved(
                    repo,
                    config,
                    catalog,
                    plan,
                    confirmation,
                    credentials,
                    client_factory=lambda token: client,
                    stage=kwargs.pop("stage", stage),
                    clock=lambda: NOW,
                    sleep=kwargs.pop("sleep", Mock()),
                    **kwargs,
                )

        prepare_next_asset(repo, config)
        yield Run()


def test_preview_is_deterministic_local_and_uses_publication_position(run):
    plan = run.preview()
    assert plan == run.preview()
    assert plan.post.id == "AED-0002" and plan.entry.position == 7
    assert plan.asset_path.name == "AED-0002.jpg" and plan.asset_sha256
    assert plan.caption == "Take note.\n\n#reflection #journaling"
    assert plan.hashtags == ("#reflection", "#journaling")
    assert (
        run.events == []
        and run.client.mock_calls == []
        and run.credentials.mock_calls == []
    )
    assert run.repo.publication_history(plan.post.id) == []


def test_success_commits_before_remote_and_is_durable_on_reopen(run):
    original = list(run.repo.list_queue())

    def create(account_id, image_url, *, caption):
        assert account_id == "103" and image_url == URL
        assert (
            caption == run.catalog.captions[1].caption + "\n\n#reflection #journaling"
        )
        assert (
            PublicationService(run.repo).state("AED-0002")
            is PublicationState.PUBLISHING
        )
        assert run.repo.list_queue() == original[1:]
        with open_repository(run.config) as other:
            assert (
                PublicationService(other).state("AED-0002")
                is PublicationState.PUBLISHING
            )
        return Container("104")

    run.client.create_image_container.side_effect = create
    result = run.publish()
    assert result.container_id == "104" and result.media_id == "105"
    assert run.events == ["open", "closed"]
    assert (
        run.client.create_image_container.call_count
        == run.client.publish_container.call_count
        == 1
    )
    with open_repository(run.config) as other:
        assert (
            PublicationService(other).state(result.post_id)
            is PublicationState.PUBLISHED
        )
        row = other.get_publication(result.attempt_id)
        assert row is not None
        assert (
            row.publication.external_id == "105" and row.publication.published_at == NOW
        )
        assert other.list_queue() == original[1:]
    next_plan = run.preview()
    assert next_plan.post.id == "AED-0001" and next_plan.entry.position == 9
    assert (
        next_plan.challenge
        != f"PUBLISH @ask.every.day AED-0002 {next_plan.fingerprint[:12]}"
    )


def test_empty_queue_does_not_call_credentials_or_client(run):
    run.repo.clear_queue()
    assert run.preview() is None
    assert run.client.mock_calls == run.credentials.mock_calls == []


@pytest.mark.parametrize(
    "answer", ["", "yes", "CONFIRM=1", "PUBLISH @wrong AED-0002 123"]
)
def test_no_approval_means_no_side_effects(run, answer):
    with pytest.raises(PublishError, match="confirmation_required"):
        run.publish(confirmation=answer)
    assert run.events == [] and run.client.mock_calls == []
    assert run.repo.publication_history("AED-0002") == []


@pytest.mark.parametrize("kind", ["missing", "invalid", "symlink"])
def test_invalid_asset_preview_and_execute(run, kind):
    path = run.config.assets_path / "AED-0002.jpg"
    path.unlink()
    if kind == "invalid":
        path.write_bytes(b"not a JPEG")
    if kind == "symlink":
        path.symlink_to(run.config.assets_path / "AED-0001.jpg")
    assert run.preview().asset_sha256 is None
    with pytest.raises(PublishError, match="invalid_asset"):
        run.publish()
    assert run.client.mock_calls == [] and run.events == []


@pytest.mark.parametrize("change", ["asset", "copy", "queue", "post"])
def test_approval_cannot_be_reused_after_preview_changes(run, change):
    plan = run.preview()
    if change == "asset":
        (run.config.assets_path / "AED-0002.jpg").unlink()
    if change == "copy":
        run.repo.update_post(replace(plan.post, caption_override="Changed."))
    if change == "queue":
        run.repo.replace_queue(
            list(reversed([QueueEntry("AED-0001", 1), QueueEntry("AED-0002", 2)]))
        )
    if change == "post":
        run.repo.update_post(replace(plan.post, prompt="Changed?"))
    with pytest.raises(PublishError, match="preview_changed"):
        run.publish(plan=plan)
    assert run.client.mock_calls == [] and run.events == []


@pytest.mark.parametrize(
    "error", [AuthError("missing_credential"), AuthError("expired_credential")]
)
def test_credential_failure_keeps_queue(run, error):
    run.credentials.get_valid_credential.side_effect = error
    with pytest.raises(PublishError, match="preflight_failed"):
        run.publish()
    assert len(run.repo.list_queue()) == 2 and run.client.mock_calls == []


@pytest.mark.parametrize(
    "identity",
    [
        InstagramIdentity("103", "wrong", "Business"),
        InstagramIdentity("999", "ask.every.day", "Business"),
        InstagramIdentity("103", "ask.every.day", "Personal"),
    ],
)
def test_wrong_account_never_stages_or_creates_attempt(run, identity):
    run.client.identify_account.return_value = identity
    with pytest.raises(PublishError, match="account_binding"):
        run.publish()
    assert run.events == [] and run.repo.publication_history("AED-0002") == []
    run.client.create_image_container.assert_not_called()


def test_real_quota_blocks_without_hardcoded_limit(run):
    run.client.publishing_quota.return_value = PublishingQuota(7, 7, 123)
    with pytest.raises(PublishError, match="quota_exhausted"):
        run.publish()
    assert run.events == [] and len(run.repo.list_queue()) == 2


@pytest.mark.parametrize(
    "error", [TimeoutError(PRIVATE), RuntimeError(PRIVATE), KeyboardInterrupt()]
)
def test_staging_failure_never_begins_attempt(run, error):
    @contextmanager
    def broken(*args, **kwargs):
        try:
            raise error
        finally:
            run.events.append("closed")
        yield

    with pytest.raises(PublishError):
        run.publish(stage=broken)
    assert run.events == ["closed"] and len(run.repo.list_queue()) == 2
    run.client.create_image_container.assert_not_called()


@pytest.mark.parametrize(
    "operation,error",
    [
        (
            "create_image_container",
            TransportError("create_image_container", None, True),
        ),
        ("create_image_container", ResponseError("create_image_container", 200, True)),
        ("publish_container", TransportError("publish_container", None, True)),
        ("publish_container", ResponseError("publish_container", 200, True)),
        ("create_image_container", KeyboardInterrupt()),
        ("container_status", KeyboardInterrupt()),
        ("publish_container", KeyboardInterrupt()),
    ],
)
def test_uncertain_post_or_interruption_retains_unresolved_and_blocks_restart(
    run, operation, error
):
    getattr(run.client, operation).side_effect = error
    with pytest.raises(PublishError) as caught:
        run.publish()
    assert caught.value.attempt_id is not None and caught.value.media_id is None
    assert PublicationService(run.repo).state("AED-0002") is PublicationState.PUBLISHING
    assert (
        len(run.repo.publication_history("AED-0002")) == 1
        and len(run.repo.list_queue()) == 1
    )
    assert run.events == ["open", "closed"]
    with open_repository(run.config) as reopened:
        with pytest.raises(PublishError, match="unresolved_publication"):
            preview_next(reopened, run.config, run.catalog)
    assert run.client.create_image_container.call_count == 1
    assert run.client.publish_container.call_count <= 1
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "status,state",
    [
        (ContainerStatus.ERROR, "Failed"),
        (ContainerStatus.EXPIRED, "Failed"),
        (ContainerStatus.PUBLISHED, "Publishing"),
        ("UNKNOWN", "Publishing"),
    ],
)
def test_status_failures_never_publish_and_cleanup(run, status, state):
    run.client.container_status.return_value = status
    with pytest.raises(PublishError):
        run.publish()
    assert PublicationService(run.repo).state("AED-0002").value == state
    run.client.publish_container.assert_not_called()
    assert run.events == ["open", "closed"]


def test_readiness_is_bounded_and_waits_with_staging_alive(run):
    run.client.container_status.side_effect = [
        ContainerStatus.IN_PROGRESS,
        ContainerStatus.FINISHED,
    ]

    def wait(seconds):
        assert seconds == 60 and run.events == ["open"]
        assert run.client.publish_container.call_count == 0

    run.publish(sleep=wait)
    assert run.client.container_status.call_count == 2


def test_never_ready_stops_after_five_checks(run):
    run.client.container_status.return_value = ContainerStatus.IN_PROGRESS
    wait = Mock()
    with pytest.raises(PublishError, match="container_timeout"):
        run.publish(sleep=wait)
    assert wait.call_count == 4 and run.client.container_status.call_count == 5
    run.client.publish_container.assert_not_called()
    assert PublicationService(run.repo).state("AED-0002") is PublicationState.PUBLISHING


@pytest.mark.parametrize("operation", ["create_image_container", "publish_container"])
def test_malformed_identifier_preserves_attempt(run, operation):
    getattr(run.client, operation).return_value = Mock(id=PRIVATE)
    with pytest.raises(PublishError, match="invalid_provider_result"):
        run.publish()
    assert PublicationService(run.repo).state("AED-0002") is PublicationState.PUBLISHING
    assert run.events == ["open", "closed"]


def test_result_write_failure_retains_known_media_and_no_retry(run, monkeypatch):
    monkeypatch.setattr(
        run.repo,
        "update_publication",
        Mock(side_effect=sqlite3.OperationalError(PRIVATE)),
    )
    with pytest.raises(PublishError, match="record_success_failed") as caught:
        run.publish()
    assert caught.value.media_id == "105" and caught.value.container_id == "104"
    assert PublicationService(run.repo).state("AED-0002") is PublicationState.PUBLISHING
    assert run.events == ["open", "closed"]
    with pytest.raises(PublishError, match="unresolved_publication"):
        run.preview()
    run.client.publish_container.assert_called_once()


def test_begin_write_failure_retains_queue_without_post(run, monkeypatch):
    monkeypatch.setattr(
        run.repo,
        "create_publication",
        Mock(side_effect=sqlite3.OperationalError(PRIVATE)),
    )
    with pytest.raises(PublishError, match="begin_attempt_failed"):
        run.publish()
    assert (
        len(run.repo.list_queue()) == 2
        and run.repo.publication_history("AED-0002") == []
    )
    run.client.create_image_container.assert_not_called()
    assert run.events == ["open", "closed"]


def test_cleanup_failure_after_success_is_not_reported_as_unpublished(run):
    @contextmanager
    def cleanup_failure(*args, **kwargs):
        with run.stage(*args, **kwargs) as media:
            yield media
        raise RuntimeError(PRIVATE)

    with pytest.raises(PublishError, match="cleanup_failed") as caught:
        run.publish(stage=cleanup_failure)
    assert caught.value.media_id == "105"
    assert PublicationService(run.repo).state("AED-0002") is PublicationState.PUBLISHED
    run.client.publish_container.assert_called_once()


def test_copy_override_and_no_tags_reuse_policy(run):
    post = run.repo.get_post("AED-0002")
    run.repo.update_post(replace(post, caption_override="Exact approved copy."))
    run.catalog = replace(run.catalog, hashtags=[])
    # Fixture operations close over the initial catalog; call the public API explicitly.
    plan = preview_next(run.repo, run.config, run.catalog)
    assert plan is not None
    assert plan.caption == "Exact approved copy." and plan.hashtags == ()


def test_invalid_state_and_unknown_post_fail_closed(run, monkeypatch):
    run.repo.create_publication(Publication("AED-0002", PublicationStatus.FAILED, NOW))
    with pytest.raises(ValueError):
        run.preview()
    run.repo.clear_queue()
    monkeypatch.setattr(run.repo, "next_queue_entry", lambda: QueueEntry("AED-9999", 1))
    with pytest.raises(PublishError, match="unknown_post"):
        run.preview()


def test_safe_structured_logs_never_contain_private_response_or_capability(run, caplog):
    run.client.publish_container.side_effect = RuntimeError(PRIVATE + URL)
    with caplog.at_level(logging.INFO), pytest.raises(PublishError) as caught:
        run.publish()
    output = "\n".join(JSONFormatter().format(record) for record in caplog.records)
    assert (
        PRIVATE not in output and URL not in output and PRIVATE not in str(caught.value)
    )
    assert "publish_next_failed" in output and "publish_container_failed" in output


@pytest.fixture
def command(run, monkeypatch):
    import sys

    from ask_every_day import publish_next_cli as cli

    monkeypatch.setattr(cli, "load_config", Mock(return_value=run.config))
    monkeypatch.setattr(cli.CopyCatalog, "load", Mock(return_value=run.catalog))
    monkeypatch.setattr(cli, "credential_manager", Mock(return_value=run.credentials))
    monkeypatch.setattr(cli, "configure_logging", Mock())
    monkeypatch.setattr(cli, "verify_editorial_fonts", Mock(return_value=[]))

    def execute(*, confirm=False, interactive=False, answer=None):
        monkeypatch.setattr(
            sys, "argv", ["publish-next"] + (["--confirm"] if confirm else [])
        )
        monkeypatch.setattr(sys.stdin, "isatty", lambda: interactive)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: interactive)
        if answer is not None:
            monkeypatch.setattr("builtins.input", answer)
        return cli.main()

    return cli, execute


def test_cli_preview_reports_full_plan_without_online_calls(run, command, capsys):
    cli, execute = command
    execute()
    output = json.loads(capsys.readouterr().out)
    assert (
        output["mode"] == "preview"
        and output["asset_valid"]
        and output["credential_ready"]
    )
    assert output["post_id"] == "AED-0002" and output["queue_position"] == 7
    assert (
        output["caption"] == run.preview().caption and output["quota"] == "not_checked"
    )
    assert output["account"] == "@ask.every.day" and output["asset_sha256"]
    run.credentials.get_valid_credential.assert_not_called()
    assert run.client.mock_calls == [] and run.events == []
    assert PRIVATE not in json.dumps(output)


def test_cli_preview_missing_credentials_is_safe_not_an_online_login(
    run, command, capsys
):
    cli, execute = command
    cli.credential_manager.side_effect = RuntimeError(PRIVATE)
    execute()
    output = capsys.readouterr()
    assert not json.loads(output.out)["credential_ready"]
    assert PRIVATE not in output.out + output.err
    assert run.client.mock_calls == []


def test_confirm_flag_alone_refuses_before_config_or_credentials(command, capsys):
    cli, execute = command
    with pytest.raises(SystemExit) as caught:
        execute(confirm=True)
    assert caught.value.code == 1
    cli.load_config.assert_not_called()
    cli.credential_manager.assert_not_called()
    assert "interactive_confirmation_required" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["", "yes", "old confirmation"])
def test_cli_interactive_mismatch_never_contacts_provider(run, command, capsys, answer):
    _, execute = command
    with pytest.raises(SystemExit):
        execute(confirm=True, interactive=True, answer=lambda _: answer)
    output = capsys.readouterr()
    assert run.preview().caption in output.out
    assert "confirmation_required" in output.err
    assert run.client.mock_calls == [] and run.events == []


def test_complete_mocked_cli_publication_reopens_database(
    run, command, monkeypatch, capsys
):
    cli, execute = command
    original = cli.publish_approved

    def injected(repo, config, catalog, plan, answer, credentials, **guards):
        return original(
            repo,
            config,
            catalog,
            plan,
            answer,
            credentials,
            client_factory=lambda _: run.client,
            stage=run.stage,
            clock=lambda: NOW,
            sleep=Mock(),
            **guards,
        )

    monkeypatch.setattr(cli, "publish_approved", injected)
    execute(confirm=True, interactive=True, answer=lambda _: run.preview().challenge)
    output = capsys.readouterr()
    result = json.loads(output.out.splitlines()[-1])
    assert result["state"] == "Published" and result["staging"] == "closed"
    assert result["container_id"] == "104" and result["media_id"] == "105"
    assert PRIVATE not in output.out + output.err and URL not in output.out + output.err


def test_cli_empty_queue_never_touches_credentials(run, command, capsys):
    cli, execute = command
    run.repo.clear_queue()
    execute()
    assert json.loads(capsys.readouterr().out)["queue"] == "empty"
    cli.credential_manager.assert_not_called()


def test_cli_cancel_before_approval_leaves_queue(run, command):
    _, execute = command

    def interrupt(_):
        raise KeyboardInterrupt()

    with pytest.raises(SystemExit):
        execute(confirm=True, interactive=True, answer=interrupt)
    assert (
        run.repo.publication_history("AED-0002") == []
        and len(run.repo.list_queue()) == 2
    )
    assert run.events == [] and run.client.mock_calls == []


def test_database_lock_excludes_overlapping_confirmed_runs_and_releases(run):
    from ask_every_day.publish_next_cli import publication_lock

    with publication_lock(run.config.database_path):
        with pytest.raises(PublishError, match="another_publisher_running"):
            with publication_lock(run.config.database_path):
                pass
    with publication_lock(run.config.database_path):
        pass


def test_restart_cannot_reuse_old_published_approval(run):
    plan = run.preview()
    run.publish(plan=plan)
    with pytest.raises(PublishError, match="preview_changed"):
        run.publish(plan=plan)
    assert (
        run.client.create_image_container.call_count
        == run.client.publish_container.call_count
        == 1
    )
    assert len(run.repo.publication_history("AED-0002")) == 1


def test_staging_checks_for_changed_queue_before_first_post(run):
    @contextmanager
    def changed(*args, **kwargs):
        with run.stage(*args, **kwargs) as media:
            run.repo.update_post(
                replace(run.repo.get_post("AED-0002"), caption_override="New copy.")
            )
            yield media

    with pytest.raises(PublishError, match="preview_changed"):
        run.publish(stage=changed)
    run.client.create_image_container.assert_not_called()
    assert run.repo.publication_history("AED-0002") == [] and run.events == [
        "open",
        "closed",
    ]


def test_caption_limit_fails_before_side_effects(run):
    post = run.repo.get_post("AED-0002")
    run.repo.update_post(replace(post, caption_override="x" * 2201))
    with pytest.raises(PublishError, match="caption_limits"):
        run.preview()
    assert run.client.mock_calls == []


def test_real_versioned_catalog_loads():
    from pathlib import Path

    catalog = CopyCatalog.load(Path("data"))
    assert len(catalog.profiles) == 400 and catalog.captions and catalog.hashtags


def test_two_confirmed_fake_publications_use_distinct_prepared_colours(run):
    first = run.preview()
    run.publish(plan=first)
    assert run.preview().asset_sha256 is None
    prepare_next_asset(run.repo, run.config)
    second = run.preview()
    assert second.palette_sequence == first.palette_sequence + 1
    assert second.background_color != first.background_color
    run.client.create_image_container.return_value = Container("106")
    run.client.publish_container.return_value = PublishedMedia("107")
    run.publish(plan=second)
    assert run.client.publish_container.call_count == 2
    assert run.repo.list_queue() == []
