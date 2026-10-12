"""Load local TOML settings without creating directories or opening a database."""

import logging
import os
import tomllib
from pathlib import Path
from typing import cast

from ask_every_day.domain import AppConfig, RendererConfig

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """A configuration file cannot be read or contains invalid settings."""


def _keys(data: dict[str, object], allowed: set[str], section: str) -> None:
    unknown = data.keys() - allowed
    if unknown:
        raise ValueError(f"unknown {section} setting(s): {', '.join(sorted(unknown))}")


def _string(data: dict[str, object], key: str) -> str:
    if key not in data:
        raise ValueError(f"missing required setting: {key}")
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    if not value.strip():
        raise ValueError(f"{key} must not be empty or whitespace")
    return value


def _dimension(data: dict[str, object], key: str, default: int) -> int:
    value = data.get(key, default)
    if type(value) is not int:
        raise TypeError(f"renderer.{key} must be an integer")
    return value


def _path(data: dict[str, object], key: str, directory: Path) -> Path:
    value = _string(data, key)
    if "\x00" in value:
        raise ValueError(f"{key} must not contain a null character")
    return Path(os.path.abspath(directory / value))


def load_config(path: str | Path = "aed.toml") -> AppConfig:
    """Read an explicit file; relative data paths use its containing directory.

    Paths are made absolute lexically, without resolving symlinks, expanding
    environment variables or checking that configured destinations exist.
    """
    source = Path(os.path.abspath(path))
    try:
        with source.open("rb") as stream:
            data = cast(dict[str, object], tomllib.load(stream))
        _keys(
            data, {"database_path", "assets_path", "account_handle", "renderer"}, "root"
        )
        raw_renderer = data.get("renderer", {})
        if not isinstance(raw_renderer, dict):
            raise TypeError("renderer must be a TOML table")
        renderer = cast(dict[str, object], raw_renderer)
        _keys(renderer, {"template", "width", "height"}, "renderer")
        defaults = RendererConfig()
        config = AppConfig(
            database_path=_path(data, "database_path", source.parent),
            assets_path=_path(data, "assets_path", source.parent),
            account_handle=_string(data, "account_handle"),
            renderer=RendererConfig(
                template=(
                    _string(renderer, "template")
                    if "template" in renderer
                    else defaults.template
                ),
                width=_dimension(renderer, "width", defaults.width),
                height=_dimension(renderer, "height", defaults.height),
            ),
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(f"Cannot load configuration {source}: {exc}") from exc
    logger.info(
        "Local configuration loaded",
        extra={"event": "config_loaded", "config_path": str(source)},
    )
    return config
