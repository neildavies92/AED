"""Configuration boundary validation and read-only path semantics."""

import logging
from pathlib import Path

import pytest

from ask_every_day.config import ConfigError, load_config
from ask_every_day.domain import RendererConfig

BASE = """database_path = "data/aed.sqlite3"
assets_path = "assets/generated"
account_handle = "@ask.every.day"
"""


def write_config(tmp_path: Path, text: str = BASE) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_custom_values(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        BASE + '[renderer]\ntemplate = "Alternate"\nwidth = 900\nheight = 1200\n',
    )
    config = load_config(path)
    assert config.database_path == tmp_path / "data/aed.sqlite3"
    assert config.assets_path == tmp_path / "assets/generated"
    assert config.account_handle == "@ask.every.day"
    assert config.renderer == RendererConfig("Alternate", 900, 1200)


def test_defaults_and_partial_override(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path)).renderer == RendererConfig()
    config = load_config(write_config(tmp_path, BASE + "[renderer]\nwidth = 800\n"))
    assert config.renderer == RendererConfig(width=800)


def test_default_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "aed.toml").write_text(BASE, encoding="utf-8")
    assert load_config().database_path == tmp_path / "data/aed.sqlite3"


@pytest.mark.parametrize("key", ["database_path", "assets_path", "account_handle"])
def test_required_fields(tmp_path: Path, key: str) -> None:
    text = "\n".join(line for line in BASE.splitlines() if not line.startswith(key))
    with pytest.raises(ConfigError, match=f"missing required setting: {key}"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (BASE + "[", "Cannot load configuration"),
        (BASE + "extra = 1", "unknown root setting"),
        (BASE + "renderer = 1", "renderer must be a TOML table"),
        (BASE + "[renderer]\nwidht = 1", "unknown renderer setting"),
        (BASE + "[renderer]\nwidth = 0", "width must be positive"),
        (BASE + "[renderer]\nheight = -1", "height must be positive"),
        (BASE + "[renderer]\nwidth = true", "width must be an integer"),
        (BASE + "[renderer]\nheight = 1.5", "height must be an integer"),
        (BASE + '[renderer]\ntemplate = " "', "template must not be empty"),
        (BASE + "[renderer]\ntemplate = 3", "template must be a string"),
        (BASE.replace('"@ask.every.day"', '" "'), "account_handle must not be empty"),
        (BASE.replace('"@ask.every.day"', "4"), "account_handle must be a string"),
        (BASE.replace('"data/aed.sqlite3"', '""'), "database_path must not be empty"),
        (BASE.replace('"data/aed.sqlite3"', "[]"), "database_path must be a string"),
        (
            BASE.replace('"data/aed.sqlite3"', '"\\u0000"'),
            "database_path must not contain a null character",
        ),
    ],
)
def test_invalid_config(tmp_path: Path, text: str, message: str) -> None:
    path = write_config(tmp_path, text)
    with pytest.raises(ConfigError, match=message) as error:
        load_config(path)
    assert str(path) in str(error.value)
    assert error.value.__cause__ is not None


def test_unreadable_sources(tmp_path: Path) -> None:
    for path in (tmp_path / "missing.toml", tmp_path):
        with pytest.raises(ConfigError, match="Cannot load configuration"):
            load_config(path)
    path = tmp_path / "invalid-encoding.toml"
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigError, match="Cannot load configuration"):
        load_config(path)


def test_path_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "settings"
    directory.mkdir()
    path = write_config(
        directory,
        BASE.replace("data/aed.sqlite3", "../db/aed.sqlite3").replace(
            "assets/generated", str(tmp_path / "absolute-assets")
        ),
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(Path("settings") / path.name)
    assert config.database_path == tmp_path / "db/aed.sqlite3"
    assert config.assets_path == tmp_path / "absolute-assets"


def test_paths_are_literal_and_symlinks_not_resolved(tmp_path: Path) -> None:
    directory = tmp_path / "settings"
    directory.mkdir()
    path = write_config(directory, BASE.replace("assets/generated", "~/$ASSETS"))
    link = tmp_path / "linked.toml"
    link.symlink_to(path)
    config = load_config(link)
    assert config.database_path == tmp_path / "data/aed.sqlite3"
    assert config.assets_path == tmp_path / "~/$ASSETS"


def test_loading_only_reads_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path)
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    opened: list[Path] = []
    original_open = Path.open

    def read_only_open(self: Path, mode: str = "r", *args: object, **kwargs: object):
        assert self == path
        assert mode == "rb"
        opened.append(self)
        return original_open(self, mode)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", read_only_open)
        load_config(path)
    assert opened == [path]
    assert set(tmp_path.rglob("*")) == set(before)
    assert {item: item.read_bytes() for item in before} == before


def test_structured_success_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_config(tmp_path)
    with caplog.at_level(logging.INFO, logger="ask_every_day.config"):
        load_config(path)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.__dict__["event"] == "config_loaded"
    assert record.__dict__["config_path"] == str(path)
    assert "@ask.every.day" not in record.getMessage()


def test_documented_example_loads() -> None:
    example = Path(__file__).parents[1] / "aed.example.toml"
    assert load_config(example).renderer == RendererConfig()


def test_configurable_brand_palette(tmp_path):
    text = (
        BASE
        + '\n[palette]\nbackgrounds=["#e2eff9","#EDF4FB","#D3E6F5"]\ntext_color="#112233"\naccent_color="#234567"\nseed=7\n'
    )
    config = load_config(write_config(tmp_path, text))
    assert config.palette.seed == 7 and config.palette.backgrounds[0] == "#e2eff9"
    assert (
        config.renderer.text_color == "#112233"
        and config.renderer.accent_color == "#234567"
    )


@pytest.mark.parametrize(
    "text",
    [
        "palette=1",
        "[palette]\nseed=true",
        "[palette]\nseed=-1",
        "[palette]\nextra=1",
        '[palette]\nbackgrounds=["#fff"]',
        "[palette]\nbackgrounds=[1,2,3]",
        '[palette]\nbackgrounds="blue"',
        '[palette]\ntext_color="#FFFFFF"',
        '[palette]\naccent_color="not-a-colour"',
    ],
)
def test_invalid_brand_configuration_fails_closed(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, BASE + "\n" + text))
