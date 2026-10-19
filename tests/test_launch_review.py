"""Small real launch pack, plus isolated policy and output failure cases."""

import csv
import logging
import socket
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from ask_every_day.caption_catalog import load_captions, load_profiles
from ask_every_day.content_import import load_hashtag_snapshot, load_snapshot
from ask_every_day.domain import AppConfig, RendererConfig
from ask_every_day.highlight import highlight_span, segment_line
from ask_every_day.jpeg_export import validate_jpeg
from ask_every_day.launch_review import (
    REVIEW_IDS,
    gallery_html,
    generate_review,
    select_review,
)
from ask_every_day.layout import layout_prompt
from ask_every_day.queue_generation import generate_queue
from ask_every_day.repository import open_repository

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def editorial():
    data = ROOT / "data"
    return (
        load_snapshot(
            data / "prompts.csv", data / "taxonomy.csv", data / "hashtags.csv"
        ),
        load_captions(data / "captions.csv", data / "taxonomy.csv"),
        load_hashtag_snapshot(data / "hashtags.csv"),
        load_profiles(
            data / "caption_profiles.csv", [f"AED-{n:04}" for n in range(1, 401)]
        ),
    )


def test_curated_coverage_and_deterministic_copy(editorial):
    posts, captions, tags, profiles = editorial
    items = select_review(posts, captions, tags, profiles)
    assert len(items) == len(set(REVIEW_IDS)) == 20
    assert tuple(x.post.id for x in items) == REVIEW_IDS
    assert items == select_review(posts[::-1], captions[::-1], tags[::-1], profiles)
    assert set(Counter(x.post.category for x in items).values()) == {2}
    assert len({x.post.category for x in items}) == 10
    assert {x.caption for x in items} <= {x.caption for x in captions}
    positions = {p.id: n for n, p in enumerate(generate_queue(posts, seed=42), 1)}
    assert all(item.position == positions[item.post.id] for item in items)
    html = gallery_html(items)
    for item in items:
        assert f"Preview queue position: {item.position}" in html
        assert f"CTA: {item.cta_role}" in html
        assert item.profile.response_mode in html
        assert item.profile.time_focus in html
    assert "Public reply: no" in html and "Public reply: yes" in html
    assert {x.hashtags.id for x in items if x.hashtags} == {
        f"TAG-{n:03}" for n in range(2, 7)
    }
    lengths = [len(x.post.prompt) for x in items]
    assert min(lengths) <= 40 and any(55 <= n <= 75 for n in lengths)
    assert max(lengths) == max(len(p.prompt) for p in posts) == 90
    assert "AED-0236" in REVIEW_IDS
    assert {len(layout_prompt(x.post.prompt)) for x in items} == {2, 3, 4, 5, 6}
    segments = {
        sum(
            bool(
                segment_line(
                    line, highlight_span(x.post.prompt, x.post.highlight_text)
                )[1]
            )
            for line in layout_prompt(x.post.prompt)
        )
        for x in items
    }
    assert segments == {1, 2, 3}
    assert all(p.highlight_text for p in posts)  # Honest production coverage gap.
    assert any("'" in x.post.prompt for x in items)
    assert any("," in x.post.prompt for x in items)
    for x in items:
        assert x.hashtags and x.post.category in x.hashtags.use_for


def test_overrides_escaping_and_synthetic_no_highlight(editorial, tmp_path):
    posts, captions, tags, profiles = editorial
    first = next(p for p in posts if p.id == REVIEW_IDS[0])
    changed = replace(
        first,
        prompt='What does "<care> & time" mean?',
        highlight_text=None,
        caption_override='  <script>alert("x")</script>\n& care  ',
        hashtag_set_id="TAG-001",
    )
    items = select_review(
        [changed if p.id == changed.id else p for p in posts], captions, tags, profiles
    )
    assert items[0].caption == changed.caption_override
    assert items[0].hashtags == tags[0]
    html = gallery_html(items)
    assert "<p>1 production Posts" in gallery_html(items[:1])
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&quot;&lt;care&gt; &amp; time&quot;" in html
    assert "white-space:pre-wrap" in html and "Caption override: yes" in html
    assert "yes</p>" in html
    generate_review(items[:1], RendererConfig(), tmp_path / "pack")
    validate_jpeg((tmp_path / "pack" / f"{changed.id}.jpg").read_bytes())


def test_real_pack_rerun_readonly_and_no_network(
    editorial, tmp_path, monkeypatch, caplog
):
    posts, captions, tags, profiles = editorial

    def denied(*args, **kwargs):
        raise AssertionError("Network forbidden")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    config = AppConfig(tmp_path / "review.db", tmp_path / "assets", "@test")
    with open_repository(config) as repository:
        repository.import_posts(posts)
    before = (
        config.database_path.read_bytes(),
        config.database_path.stat().st_mtime_ns,
    )
    with sqlite3.connect(
        config.database_path.as_uri() + "?mode=ro", uri=True
    ) as connection:
        from ask_every_day.repository import SQLiteRepository

        items = select_review(
            SQLiteRepository(connection).list_posts(), captions, tags, profiles
        )
    output = tmp_path / "nested" / "launch-review"
    caplog.set_level(logging.INFO, logger="ask_every_day")
    index = generate_review(items, config.renderer, output)
    original = {p.name: p.read_bytes() for p in output.iterdir()}
    assert set(original) == {"index.html"} | {f"{p}.jpg" for p in REVIEW_IDS}
    assert index == output / "index.html"
    for name, data in original.items():
        if name.endswith(".jpg"):
            validate_jpeg(data)  # Authoritative full decoding, RGB and 1080x1350.
    generate_review(items, config.renderer, output)
    assert original == {p.name: p.read_bytes() for p in output.iterdir()}
    assert before == (
        config.database_path.read_bytes(),
        config.database_path.stat().st_mtime_ns,
    )
    assert not list(output.parent.glob(".launch-review-*"))
    assert not any(
        x.post.prompt in caplog.text or x.caption in caplog.text for x in items
    )
    assert {r.event for r in caplog.records if hasattr(r, "event")} >= {
        "launch_review_started",
        "launch_review_completed",
    }
    assert (
        subprocess.run(
            ["git", "check-ignore", "output/launch-review/index.html"],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
    )


@pytest.mark.parametrize(
    "kind", ["file", "symlink", "unexpected", "child_symlink", "directory"]
)
def test_output_rejects_surprises(editorial, tmp_path, kind):
    items = select_review(*editorial)
    output = tmp_path / "pack"
    if kind == "file":
        output.write_text("keep")
    elif kind == "symlink":
        output.symlink_to(tmp_path, target_is_directory=True)
    else:
        output.mkdir()
        target = output / (
            f"{REVIEW_IDS[0]}.jpg" if kind != "unexpected" else "notes.txt"
        )
        if kind == "child_symlink":
            target.symlink_to(tmp_path / "absent")
        elif kind == "directory":
            target.mkdir()
        else:
            target.write_text("keep")
    with pytest.raises(ValueError):
        generate_review(items, RendererConfig(), output)


def test_failed_render_preserves_existing_pack(editorial, tmp_path, monkeypatch):
    items = select_review(*editorial)
    output = tmp_path / "pack"
    output.mkdir()
    (output / "index.html").write_text("previous")

    def fail(*args, **kwargs):
        raise ValueError("private editorial payload")

    monkeypatch.setattr("ask_every_day.launch_review.render_svg", fail)
    with pytest.raises(ValueError):
        generate_review(items, RendererConfig(), output)
    assert (output / "index.html").read_text() == "previous"
    assert not list(tmp_path.glob(".launch-review-*"))


@pytest.mark.parametrize(
    "contents",
    [
        "wrong,columns\n",
        "id,caption,active,notes\nCAP-001,Hi,MAYBE,x\n",
        "id,caption,active,notes\nCAP-001,Hi,TRUE\n",
        'id,caption,active,notes\n"unclosed',
    ],
)
def test_caption_parser_rejects_malformed(tmp_path, contents):
    source = tmp_path / "captions.csv"
    source.write_text(contents)
    with pytest.raises((ValueError, csv.Error)):
        load_captions(source, ROOT / "data/taxonomy.csv")


def test_selection_rejects_missing_duplicate(editorial):
    posts, captions, tags, profiles = editorial
    with pytest.raises(ValueError, match="missing"):
        select_review([], captions, tags, profiles)
    with pytest.raises(ValueError, match="Duplicate"):
        select_review([*posts, posts[0]], captions, tags, profiles)


def test_cli_missing_database_is_not_created(tmp_path):
    config = tmp_path / "custom.toml"
    config.write_text(
        'database_path="missing.db"\nassets_path="assets"\naccount_handle="@test"\n'
    )
    result = subprocess.run(
        [sys.executable, "-m", "ask_every_day.launch_review", "--config", str(config)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert not (tmp_path / "missing.db").exists()
    assert not (tmp_path / "output").exists()
    assert '"event":"launch_review_failed"' in result.stderr


def test_cli_custom_config_and_read_only_state(editorial, tmp_path):
    posts, _, _, _ = editorial
    config = AppConfig(tmp_path / "content.db", tmp_path / "unused-assets", "@test")
    with open_repository(config) as repository:
        repository.import_posts(posts)
    before = (
        config.database_path.read_bytes(),
        config.database_path.stat().st_mtime_ns,
    )
    settings = tmp_path / "custom.toml"
    settings.write_text(
        'database_path="content.db"\nassets_path="unused-assets"\naccount_handle="@test"\n'
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ask_every_day.launch_review",
            "--config",
            str(settings),
            "--captions",
            str(ROOT / "data/captions.csv"),
            "--hashtags",
            str(ROOT / "data/hashtags.csv"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Generated 20 review items" in result.stdout
    assert '"event":"launch_review_completed"' in result.stderr
    assert (tmp_path / "output/launch-review/index.html").exists()
    assert len(list((tmp_path / "output/launch-review").glob("*.jpg"))) == 20
    assert not config.assets_path.exists()
    assert before == (
        config.database_path.read_bytes(),
        config.database_path.stat().st_mtime_ns,
    )
    assert all(p.prompt not in result.stderr for p in posts)
