"""Pure palette policy is stable across IDs, processes and global random state."""

import random
from dataclasses import asdict, replace

import pytest

from ask_every_day.palette import Assignment, PaletteConfig, contrast, next_assignment


@pytest.mark.parametrize(
    "backgrounds",
    [
        (),
        ("#E2EFF9",),
        ("#E2EFF9",) * 3,
        ("#e2eff9", "#E2EFF9", "#D3E6F5"),
        ("blue", "#EDF4FB", "#D3E6F5"),
        ("#fff", "#EDF4FB", "#D3E6F5"),
        ("#00000000", "#EDF4FB", "#D3E6F5"),
    ],
)
def test_invalid_palette_rejected(backgrounds):
    with pytest.raises(ValueError):
        PaletteConfig(backgrounds=backgrounds)


@pytest.mark.parametrize(
    "changes",
    [
        dict(seed=True),
        dict(seed=-1),
        dict(seed=2**63),
        dict(seed=1.5),
        dict(text_color="#FFFFFF"),
        dict(accent_color="red"),
        dict(backgrounds=["#E2EFF9", "#EDF4FB", "#D3E6F5"]),
    ],
)
def test_invalid_types_and_readability(changes):
    with pytest.raises((TypeError, ValueError)):
        PaletteConfig(**changes)


def test_default_palette_and_contrast():
    config = PaletteConfig()
    assert config.backgrounds == ("#E2EFF9", "#EDF4FB", "#D3E6F5")
    for bg in config.backgrounds:
        assert contrast(config.text_color, bg) >= 4.5
        assert contrast(config.accent_color, bg) >= 4.5


@pytest.mark.parametrize("seed", range(20))
def test_balanced_bags_and_no_duplicates_for_long_sequences(seed):
    config = PaletteConfig(seed=seed)
    previous = None
    output = []
    for index in range(300):
        item = next_assignment(config, previous)
        assert item.sequence == index + 1 and item.slot == index % 3
        if previous:
            assert item.background != previous.background
            if item.slot == 0:
                assert item.bag != previous.bag
        output.append(item.background)
        previous = item
    for i in range(0, len(output), 3):
        assert set(output[i : i + 3]) == set(config.backgrounds)


def test_determinism_and_no_random_state_mutation():
    state = random.getstate()
    first = next_assignment(PaletteConfig(), None)
    assert random.getstate() == state
    random.seed(987)
    try:
        assert next_assignment(PaletteConfig(), None) == first
        # Persisted values round-trip without a process-local RNG cursor.
        recovered = Assignment(**asdict(first))
        assert next_assignment(PaletteConfig(), recovered) == next_assignment(
            PaletteConfig(), first
        )
    finally:
        random.setstate(state)
    assert (
        len({next_assignment(PaletteConfig(seed=s), None).bag for s in range(20)}) > 1
    )


def test_seed_change_finishes_existing_bag_before_new_shuffle():
    config = PaletteConfig()
    first = next_assignment(config, None)
    changed = replace(config, seed=123)
    second = next_assignment(changed, first)
    third = next_assignment(changed, second)
    assert second.bag == third.bag == first.bag
    assert second.palette_key == third.palette_key == first.palette_key
    assert {p.background for p in (first, second, third)} == set(config.backgrounds)
    fourth = next_assignment(changed, third)
    assert fourth.palette_key == changed.key and fourth.background != third.background


def test_new_background_values_require_a_completed_bag():
    config = PaletteConfig()
    first = next_assignment(config, None)
    changed = replace(config, backgrounds=("#FFFFFF", "#EDF4FB", "#D3E6F5"))
    with pytest.raises(ValueError, match="palette_change_requires_bag_boundary"):
        next_assignment(changed, first)
    third = next_assignment(config, next_assignment(config, first))
    assert next_assignment(changed, third).background != third.background


@pytest.mark.parametrize("boundary", PaletteConfig().backgrounds)
def test_legacy_boundary_is_respected(boundary):
    assert (
        next_assignment(PaletteConfig(), None, previous_background=boundary).background
        != boundary
    )
