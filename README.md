# Ask Every Day

Ask Every Day is a local-first Python automation project for the Instagram account
@ask.every.day. This foundation provides an installable package and development
tools. Application features will be added in later tickets.

## Prerequisites

- Python 3.13+ (development uses Python 3.13, pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Make (for the convenience commands below)

Run the following commands from the repository root. `uv sync` creates `.venv`,
installs the package and development dependencies from `uv.lock`, and downloads
the selected Python version if needed.

## Set up

```sh
make setup
```

This runs `uv sync`. All check targets use `uv run`, which automatically uses
the project `.venv`; manual activation is unnecessary.

## Development checks

Run `make` or `make help` to list the available commands.

| Command | Action |
| --- | --- |
| `make test` | Run pytest |
| `make lint` | Run Ruff lint checks |
| `make format` | Apply Ruff formatting |
| `make format-check` | Check formatting without editing files |
| `make typecheck` | Run Pyright |
| `make check` | Run tests, lint, formatting checks, and type checking |

The underlying `uv` commands remain available in the Makefile.
There is no application entry point yet; add a `make run` target when one exists.
Keep the Makefile and this command list up to date whenever frequently used
setup, testing, or application commands change.

Package code lives in `src/ask_every_day/` and tests in `tests/`.
Commit `uv.lock` to keep dependency versions reproducible.
