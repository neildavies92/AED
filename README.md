# Ask Every Day

Ask Every Day is a local-first Python automation project for the Instagram account
@ask.every.day. This foundation provides an installable package and development
tools. Application features will be added in later tickets.

## Prerequisites

- Python 3.13+ (development uses Python 3.13, pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Run the following commands from the repository root. `uv sync` creates `.venv`,
installs the package and development dependencies from `uv.lock`, and downloads
the selected Python version if needed.

## Set up

```sh
uv sync
```

## Development checks

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Package code lives in `src/ask_every_day/` and tests in `tests/`.
Commit `uv.lock` to keep dependency versions reproducible.
