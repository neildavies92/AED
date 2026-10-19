"""Editorial metadata validation and independent real-queue caption invariants."""

import csv
import os
import random
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from ask_every_day.caption_catalog import (
    CAPTION_COLUMNS,
    PROFILE_COLUMNS,
    load_captions,
    load_profiles,
    profile_counts,
    validate_reachability,
)
from ask_every_day.caption_review import EMOJI_PALETTE, audit_queue
from ask_every_day.content_import import load_snapshot
from ask_every_day.copy_policy import Caption, CaptionProfile, select_caption
from ask_every_day.domain import Post

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def library():
    posts = load_snapshot(
        DATA / "prompts.csv", DATA / "taxonomy.csv", DATA / "hashtags.csv"
    )
    return (
        posts,
        load_captions(DATA / "captions.csv", DATA / "taxonomy.csv"),
        load_profiles(DATA / "caption_profiles.csv", [p.id for p in posts]),
    )


def write_rows(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(rows)


def rows(name):
    with (DATA / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_profile_snapshot_and_reviewed_examples(library):
    posts, _, profiles = library
    assert len(profiles) == 400
    assert (
        set(profiles) == {p.id for p in posts} == {f"AED-{n:04}" for n in range(1, 401)}
    )
    assert {p.prompt_form for p in profiles.values()} == {"question"}
    assert all(p.prompt.endswith("?") for p in posts)  # QA, never runtime inference.
    counts = profile_counts(profiles.values())
    assert counts == profile_counts(list(reversed(list(profiles.values()))))
    assert counts["response_mode"] == {
        "act": 19,
        "appreciate": 14,
        "compare": 30,
        "decide": 41,
        "imagine": 71,
        "notice": 73,
        "recall": 50,
        "reflect": 81,
        "relate": 21,
    }
    assert counts["time_focus"] == {
        "future": 79,
        "mixed": 53,
        "past": 44,
        "present": 134,
        "timeless": 90,
    }
    assert counts["public_reply_ok"] == {"False": 151, "True": 249}
    assert profiles["AED-0013"].response_mode == "recall"
    assert profiles["AED-0030"].response_mode == "appreciate"
    assert profiles["AED-0236"].response_mode == "compare"
    for n in [15, 16, 47, 77, 150, 155, 167, 175, 184, 251, 256]:
        assert not profiles[f"AED-{n:04}"].public_reply_ok


def test_catalogue_integrity_and_editorial_bounds(library):
    posts, captions, profiles = library
    assert len(captions) == 134
    assert [c.id for c in captions] == [f"CAP-{n:03}" for n in range(1, 135)]
    assert all(c.active for c in captions)
    targeted = [c for c in captions if c.category != "*"]
    counts = Counter((c.category, c.subcategory) for c in targeted)
    assert len(targeted) == 120 and len(counts) == 40 and set(counts.values()) == {3}
    assert set(counts) == {(p.category, p.subcategory) for p in posts}
    assert len(captions) - len(targeted) == 14
    validate_reachability(posts, captions, profiles)
    assert max(len(c.caption) for c in captions) <= 120
    emoji_counts = Counter(
        sum(c.caption.count(e) for e in EMOJI_PALETTE) for c in captions
    )
    assert set(emoji_counts) <= {0, 1}
    assert 0.20 <= emoji_counts[1] / len(captions) <= 0.30
    for c in captions:
        plain = c.caption
        for emoji in EMOJI_PALETTE:
            plain = plain.replace(emoji, "")
        assert all(ord(char) < 128 for char in plain)  # This committed palette only.
    assert [c.caption for c in captions[:12]] == [
        "Take a few minutes with this one. 🪞",
        "No right answer. Just your answer.",
        "Write the first thing that comes to mind.",
        "Save this for your next quiet moment.",
        "A question worth sitting with today.",
        "Be honest with yourself on this one.",
        "What comes up for you?",
        "Give yourself five uninterrupted minutes with this.",
        "Your answer might surprise you.",
        "Come back to this question tonight.",
        "Write without editing yourself.",
        "One question. See where it takes you.",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "CAP-000"),
        ("id", "CAP-1"),
        ("caption", " "),
        ("active", "true"),
        ("category", "Unknown"),
        ("subcategory", "Identity"),
        ("cta_role", "like"),
        ("prompt_forms", "question|*"),
        ("response_modes", "recall||reflect"),
        ("response_modes", "guess"),
        ("time_focus", "yesterday"),
        ("public_reply_required", "yes"),
        ("public_reply_required", "TRUE"),
        ("prompt_forms", "question|question"),
    ],
)
def test_invalid_caption_rows_fail(tmp_path, field, value):
    content = rows("captions.csv")
    content[0][field] = value
    path = tmp_path / "captions.csv"
    write_rows(path, CAPTION_COLUMNS, content)
    with pytest.raises((ValueError, TypeError)):
        load_captions(path, DATA / "taxonomy.csv")


@pytest.mark.parametrize("kind", ["id", "text", "shape", "columns"])
def test_duplicate_or_malformed_catalogue(tmp_path, kind):
    content = rows("captions.csv")
    if kind == "id":
        content[1]["id"] = content[0]["id"]
    if kind == "text":
        content[1]["caption"] = "  " + content[0]["caption"].upper() + "\n"
    path = tmp_path / "captions.csv"
    write_rows(path, CAPTION_COLUMNS, content)
    if kind == "shape":
        path.write_text(path.read_text() + "CAP-999,too,few\n")
    if kind == "columns":
        path.write_text(path.read_text().replace("id,caption,", "id,id,", 1))
    with pytest.raises(ValueError):
        load_captions(path, DATA / "taxonomy.csv")


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "extra",
        "duplicate",
        "form",
        "mode",
        "time",
        "bool",
        "shape",
        "columns",
    ],
)
def test_invalid_profiles_fail_atomically(tmp_path, kind):
    content = rows("caption_profiles.csv")
    if kind == "missing":
        content.pop()
    if kind == "extra":
        content.append({**content[0], "post_id": "AED-0401"})
    if kind == "duplicate":
        content[-1] = content[0]
    for name, field in [
        ("form", "prompt_form"),
        ("mode", "response_mode"),
        ("time", "time_focus"),
        ("bool", "public_reply_ok"),
    ]:
        if kind == name:
            content[0][field] = "invalid"
    path = tmp_path / "profiles.csv"
    write_rows(path, PROFILE_COLUMNS, content)
    if kind == "shape":
        path.write_text(path.read_text() + "AED-9999,question\n")
    if kind == "columns":
        path.write_text(path.read_text().replace("post_id,", "wrong,", 1))
    with pytest.raises((ValueError, TypeError)):
        load_profiles(path, [f"AED-{n:04}" for n in range(1, 401)])


def values():
    post = Post(
        "AED-0001", "What feels like home?", "Self-awareness", "Identity", "Classic"
    )
    profile = CaptionProfile(post.id, "question", "reflect", "timeless", True)
    return post, profile


def test_exact_override_precedes_all_policy_requirements():
    post, _ = values()
    text = "  Exact, unedited.\n第二行 🪞  "
    result = select_caption(
        replace(post, caption_override=text), [], None, position=None
    )
    assert (
        result.caption == text and result.caption_id is None and result.cta_role is None
    )


def test_exact_pair_order_stable_ids_and_inactive_exclusion():
    post, profile = values()
    general = Caption("CAP-001", "General")
    first = Caption(
        "CAP-002", "First", category=post.category, subcategory=post.subcategory
    )
    second = replace(first, id="CAP-003", caption="Second")
    pool = [general, second, first]
    assert select_caption(post, pool, profile, position=1).caption == "First"
    assert select_caption(post, pool[::-1], profile, position=1).caption == "First"
    assert select_caption(post, pool, profile, position=2).caption == "Second"
    assert select_caption(
        post, [general, replace(first, active=False)], profile, position=1
    ).general_fallback
    assert select_caption(post, [first], profile, position=None).caption == "First"


@pytest.mark.parametrize(
    "profile_change,caption_change",
    [
        ({"prompt_form": "statement"}, {"prompt_forms": ("question",)}),
        ({"response_mode": "imagine"}, {"response_modes": ("recall",)}),
        ({"time_focus": "timeless"}, {"time_focus": ("past", "mixed")}),
        (
            {"public_reply_ok": False},
            {"cta_role": "comment_reply", "public_reply_required": True},
        ),
    ],
)
def test_incompatible_copy_is_never_selected(profile_change, caption_change):
    post, profile = values()
    profile = replace(profile, **profile_change)
    unsuitable = Caption(
        "CAP-001", "Would your answer have been different a year ago?", **caption_change
    )
    safe = Caption("CAP-002", "Give this a quiet moment.")
    assert (
        select_caption(post, [unsuitable, safe], profile, position=3).caption
        == safe.caption
    )
    with pytest.raises(ValueError, match="No compatible"):
        select_caption(post, [unsuitable], profile, position=3)


def test_cadence_and_safe_fallback_order():
    post, profile = values()
    pool = [
        Caption("CAP-001", "Reflect"),
        Caption("CAP-002", "Save", cta_role="save_return"),
        Caption(
            "CAP-003",
            "Reply if you like",
            cta_role="comment_reply",
            public_reply_required=True,
        ),
        Caption("CAP-004", "Follow", cta_role="follow"),
    ]
    for position, expected in [
        (1, "reflect"),
        (3, "comment_reply"),
        (9, "save_return"),
        (30, "follow"),
        (90, "follow"),
    ]:
        assert (
            select_caption(post, pool, profile, position=position).cta_role == expected
        )
    private = replace(profile, public_reply_ok=False)
    assert select_caption(post, pool, private, position=3).cta_role == "save_return"
    assert (
        select_caption(post, [pool[0], pool[2]], private, position=3).cta_role
        == "reflect"
    )
    assert (
        select_caption(post, pool[:2], profile, position=30).cta_role == "save_return"
    )
    assert select_caption(post, pool[:1], profile, position=30).cta_role == "reflect"
    assert select_caption(post, pool, profile, position=None).cta_role == "reflect"
    # Desired General role is considered before a targeted fallback role.
    targeted = replace(pool[0], category=post.category, subcategory=post.subcategory)
    assert (
        select_caption(post, [targeted, pool[-1]], profile, position=30).cta_role
        == "follow"
    )


def test_missing_profiles_duplicates_and_no_candidate_fail():
    post, profile = values()
    caption = Caption("CAP-001", "Keep a quiet moment.")
    for wrong in [None, replace(profile, post_id="AED-0002")]:
        with pytest.raises(ValueError, match="profile"):
            select_caption(post, [caption], wrong, position=1)
    for pool in [
        [caption, caption],
        [caption, replace(caption, id="CAP-002", caption=" KEEP A QUIET MOMENT. ")],
    ]:
        with pytest.raises(ValueError, match="Duplicate"):
            select_caption(post, pool, profile, position=1)
    with pytest.raises(ValueError, match="No compatible"):
        select_caption(post, [], profile, position=1)
    for invalid in [0, -1, True, 1.5, "1"]:
        with pytest.raises(ValueError, match="position"):
            select_caption(post, [caption], profile, position=invalid)


def test_statement_profile_loader_and_safe_preview(tmp_path):
    path = tmp_path / "profiles.csv"
    write_rows(
        path,
        PROFILE_COLUMNS,
        [
            dict(
                zip(
                    PROFILE_COLUMNS,
                    ["AED-0001", "statement", "notice", "present", "TRUE"],
                )
            )
        ],
    )
    profile = load_profiles(path, ["AED-0001"])["AED-0001"]
    post = Post(
        "AED-0001", "There is room to pause.", "Everyday Life", "Attention", "Classic"
    )
    pool = [
        Caption("CAP-001", "What is your answer?", prompt_forms=("question",)),
        Caption("CAP-002", "Take a moment here."),
    ]
    assert select_caption(post, pool, profile, position=None).caption == pool[1].caption


def test_selection_does_not_use_random_state_or_process_hash():
    code = """from ask_every_day.copy_policy import Caption, CaptionProfile, select_caption
from ask_every_day.domain import Post
post=Post("AED-0013","A question?","C","S","Classic")
profile=CaptionProfile(post.id,"question","reflect","timeless",True)
print(select_caption(post,[Caption("CAP-002","Second"),Caption("CAP-001","First")],profile,position=1).caption)
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for seed in (0, 1, 12345)
    ]
    assert outputs == ["First\n"] * 3
    before = random.getstate()
    post, profile = values()
    select_caption(post, [Caption("CAP-001", "Quiet")], profile, position=1)
    assert random.getstate() == before


@pytest.mark.parametrize("seed", [0, 1, 7, 19, 42, 49, 66, 88, 99])
def test_real_queues_are_compatible_and_balanced(library, seed):
    posts, captions, profiles = library
    selections, metrics = audit_queue(posts, captions, profiles, seed=seed)
    assert len(selections) == 400
    by_id = {c.id: c for c in captions}
    for post, selection in selections:
        assert selection.caption.strip()
        c = by_id[selection.caption_id]
        p = profiles[post.id]
        assert c.active
        assert (c.category, c.subcategory) in [
            (post.category, post.subcategory),
            ("*", "*"),
        ]
        for options, value in [
            (c.prompt_forms, p.prompt_form),
            (c.response_modes, p.response_mode),
            (c.time_focus, p.time_focus),
        ]:
            assert options == ("*",) or value in options
        assert not c.public_reply_required or p.public_reply_ok
    roles = Counter(s.cta_role for _, s in selections)
    assert 0.65 <= (roles["reflect"] + roles["journal"]) / 400 <= 0.70
    assert 0.08 <= roles["comment_reply"] / 400 <= 0.25
    assert 0.05 <= roles["save_return"] / 400 <= 0.24
    assert 0.03 <= roles["follow"] / 400 <= 0.05
    assert metrics["unsafe_comments"] == 0
    assert metrics["semantic_failures"] == 0
    assert metrics["maximum_strong_run"] == 1
    assert isinstance(metrics["minimum_repetition_distance"], int)
    assert isinstance(metrics["general_fallbacks"], int)
    assert isinstance(metrics["emoji_selections"], int)
    assert metrics["minimum_repetition_distance"] >= 6
    assert metrics["general_fallbacks"] <= 40
    assert metrics["emoji_selections"] <= 160


def test_unreachable_active_caption_is_rejected(library):
    posts, captions, profiles = library
    unreachable = replace(captions[0], prompt_forms=("statement",))
    with pytest.raises(ValueError, match="Unreachable active caption: CAP-001"):
        validate_reachability(posts, [unreachable], profiles)
    validate_reachability(posts, [replace(unreachable, active=False)], profiles)


def test_review_cli_writes_only_local_reports_and_safe_logs(tmp_path):
    import json
    import shutil

    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    result = subprocess.run(
        [sys.executable, "-m", "ask_every_day.caption_review", "--seeds", "1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    output = tmp_path / "output"
    with (output / "caption-review.csv").open(encoding="utf-8", newline="") as stream:
        report = list(csv.DictReader(stream))
    assert len(report) == 400
    assert [int(r["position"]) for r in report] == list(range(1, 401))
    assert len({r["post_id"] for r in report}) == 400
    metrics = json.loads((output / "caption-review-metrics.json").read_text())
    assert [m["seed"] for m in metrics] == [0, 42]
    assert all(m["unsafe_comments"] == m["semantic_failures"] == 0 for m in metrics)
    assert '"event":"caption_review_completed"' in result.stderr
    assert all(
        r["caption"] not in result.stderr and r["prompt"] not in result.stderr
        for r in report
    )
    (data / "caption_profiles.csv").write_text("private malformed input\n")
    failed = subprocess.run(
        [sys.executable, "-m", "ask_every_day.caption_review", "--seeds", "1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    assert "private malformed input" not in failed.stderr
    assert '"event":"caption_review_failed"' in failed.stderr


def test_past_comparison_copy_requires_both_mode_and_time():
    post, profile = values()
    caption = Caption(
        "CAP-001",
        "Would your answer have been different a year ago?",
        prompt_forms=("question",),
        response_modes=("compare",),
        time_focus=("mixed",),
    )
    for candidate in [
        profile,
        replace(profile, response_mode="compare"),
        replace(profile, time_focus="mixed"),
    ]:
        with pytest.raises(ValueError, match="No compatible"):
            select_caption(post, [caption], candidate, position=1)
    allowed = replace(profile, response_mode="compare", time_focus="mixed")
    assert (
        select_caption(post, [caption], allowed, position=1).caption == caption.caption
    )
