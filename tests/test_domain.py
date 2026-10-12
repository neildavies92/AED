from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ask_every_day.domain import (
    AppConfig,
    Post,
    Publication,
    PublicationStatus,
    QueueEntry,
    RendererConfig,
)

POST = Post(
    id="AED-0001",
    prompt="Who are you when no one needs anything from you?",
    category="Self-awareness",
    subcategory="Identity",
    highlight_text="no one needs anything from you",
    template="Classic",
)
ATTEMPTED = datetime(2026, 10, 12, 12, tzinfo=UTC)


def test_valid_post_preserves_editorial_text() -> None:
    post = replace(POST, prompt="  Who are you?  ", highlight_text="Who")
    assert post.prompt == "  Who are you?  "
    assert replace(POST, highlight_text=None).highlight_text is None
    assert replace(POST, id="AED-9999").id == "AED-9999"


@pytest.mark.parametrize(
    "post_id",
    [
        "",
        " ",
        "AED-0000",
        "AED-001",
        "AED-10000",
        "aed-0001",
        "AED-０００１",
        "AED-0001\n",
    ],
)
def test_invalid_ids_across_models(post_id: str) -> None:
    with pytest.raises(ValueError, match="post ID"):
        replace(POST, id=post_id)
    with pytest.raises(ValueError, match="post ID"):
        QueueEntry(post_id, 1)
    with pytest.raises(ValueError, match="post ID"):
        Publication(post_id, PublicationStatus.PUBLISHING, ATTEMPTED)


@pytest.mark.parametrize("field", ["prompt", "category", "subcategory", "template"])
@pytest.mark.parametrize("value", ["", " \t\n"])
def test_required_post_text(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        replace(POST, **{field: value})


@pytest.mark.parametrize("highlight", ["", " ", "not in prompt", "No one"])
def test_invalid_highlight(highlight: str) -> None:
    with pytest.raises(ValueError, match="highlight_text"):
        replace(POST, highlight_text=highlight)


@pytest.mark.parametrize("status", list(PublicationStatus))
def test_valid_publication_snapshots(status: PublicationStatus) -> None:
    publication = Publication(
        post_id=POST.id,
        status=status,
        attempted_at=ATTEMPTED,
        published_at=ATTEMPTED if status is PublicationStatus.PUBLISHED else None,
        external_id="provider-result"
        if status is PublicationStatus.PUBLISHED
        else None,
        error="Publish failed" if status is PublicationStatus.FAILED else None,
    )
    assert publication.status is status
    assert PublicationStatus(status.value) is status


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        PublicationStatus("unknown")
    with pytest.raises(TypeError, match="PublicationStatus"):
        replace(
            Publication(POST.id, PublicationStatus.PUBLISHING, ATTEMPTED),
            status="Publishing",
        )


class NoOffset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> None:
        return None


@pytest.mark.parametrize(
    "timestamp", [datetime(2026, 10, 12), ATTEMPTED.replace(tzinfo=NoOffset())]
)
@pytest.mark.parametrize("field", ["attempted_at", "published_at"])
def test_timestamps_require_timezone_offset(field: str, timestamp: datetime) -> None:
    publication = Publication(
        POST.id, PublicationStatus.PUBLISHED, ATTEMPTED, ATTEMPTED
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(publication, **{field: timestamp})


def test_publication_compares_instants_across_timezones() -> None:
    completed = ATTEMPTED.astimezone(timezone(timedelta(hours=1)))
    publication = Publication(
        POST.id, PublicationStatus.PUBLISHED, ATTEMPTED, completed
    )
    assert publication.published_at == ATTEMPTED
    with pytest.raises(ValueError, match="precede"):
        replace(publication, published_at=completed - timedelta(seconds=1))


def test_publication_result_consistency() -> None:
    with pytest.raises(ValueError, match="required"):
        Publication(POST.id, PublicationStatus.PUBLISHED, ATTEMPTED)
    for status in (PublicationStatus.PUBLISHING, PublicationStatus.FAILED):
        with pytest.raises(ValueError, match="only valid"):
            Publication(POST.id, status, ATTEMPTED, ATTEMPTED)
    with pytest.raises(ValueError, match="error"):
        Publication(
            POST.id, PublicationStatus.PUBLISHED, ATTEMPTED, ATTEMPTED, error="failure"
        )
    with pytest.raises(ValueError, match="error"):
        Publication(POST.id, PublicationStatus.PUBLISHING, ATTEMPTED, error="failure")


@pytest.mark.parametrize("field", ["external_id", "error"])
def test_optional_result_text_cannot_be_blank(field: str) -> None:
    publication = Publication(POST.id, PublicationStatus.FAILED, ATTEMPTED)
    with pytest.raises(ValueError, match=field):
        replace(publication, **{field: " "})


def test_queue_position_is_one_based() -> None:
    assert QueueEntry(POST.id, 1).position == 1
    for position in (0, -1):
        with pytest.raises(ValueError, match="position"):
            QueueEntry(POST.id, position)
    with pytest.raises(TypeError, match="integer"):
        QueueEntry(POST.id, True)


def test_configuration_values() -> None:
    config = AppConfig(Path("state/aed.sqlite3"), Path("output"), "@ask.every.day")
    assert config.database_path == Path("state/aed.sqlite3")
    assert config.renderer == RendererConfig("Classic", 1080, 1350)
    assert RendererConfig("Alternate", 100, 200).width == 100


@pytest.mark.parametrize("field", ["width", "height"])
@pytest.mark.parametrize("value", [0, -1])
def test_positive_renderer_dimensions(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        replace(RendererConfig(), **{field: value})


def test_config_validation() -> None:
    config = AppConfig(Path("state.db"), Path("output"), "@ask.every.day")
    with pytest.raises(ValueError, match="account_handle"):
        replace(config, account_handle=" ")
    with pytest.raises(ValueError, match="template"):
        RendererConfig(template=" ")
    for field in ("database_path", "assets_path"):
        with pytest.raises(TypeError, match="Path"):
            replace(config, **{field: "path"})
    with pytest.raises(TypeError, match="RendererConfig"):
        replace(config, renderer="Classic")
    with pytest.raises(TypeError, match="integer"):
        RendererConfig(width=True)


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (POST, "prompt", 42),
        (POST, "id", None),
        (
            Publication(POST.id, PublicationStatus.PUBLISHING, ATTEMPTED),
            "attempted_at",
            "today",
        ),
        (QueueEntry(POST.id, 1), "position", 1.5),
    ],
)
def test_wrong_runtime_types(
    model: Post | Publication | QueueEntry, field: str, value: object
) -> None:
    with pytest.raises(TypeError):
        replace(model, **{field: value})


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (POST, "prompt"),
        (Publication(POST.id, PublicationStatus.PUBLISHING, ATTEMPTED), "status"),
        (QueueEntry(POST.id, 1), "position"),
        (RendererConfig(), "width"),
        (
            AppConfig(Path("state.db"), Path("output"), "@ask.every.day"),
            "account_handle",
        ),
    ],
)
def test_models_are_immutable(
    model: Post | Publication | QueueEntry | RendererConfig | AppConfig, field: str
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(model, field, None)


def test_publication_ordering_across_clock_change() -> None:
    london = ZoneInfo("Europe/London")
    first = datetime(2026, 10, 25, 1, 45, tzinfo=london, fold=0)
    later = datetime(2026, 10, 25, 1, 15, tzinfo=london, fold=1)
    assert (
        Publication(POST.id, PublicationStatus.PUBLISHED, first, later).published_at
        == later
    )
    with pytest.raises(ValueError, match="precede"):
        Publication(POST.id, PublicationStatus.PUBLISHED, later, first)
