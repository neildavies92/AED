.DEFAULT_GOAL := help

.PHONY: help setup test lint format format-check typecheck check

help:
	@echo "make setup         Install dependencies into .venv"
	@echo "make test          Run tests"
	@echo "make lint          Run Ruff lint checks"
	@echo "make format        Format Python files with Ruff"
	@echo "make format-check  Check formatting without changing files"
	@echo "make typecheck     Run Pyright"
	@echo "make check         Run all development checks"

setup:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run pyright

check: test lint format-check typecheck
