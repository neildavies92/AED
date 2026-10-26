.DEFAULT_GOAL := help

# Carry any Make-supplied fallback through macOS's protected shell boundary.
CAIRO_RUN = AED_CAIRO_FALLBACK="$(DYLD_FALLBACK_LIBRARY_PATH)" sh scripts/with-cairo.sh

.PHONY: render-next publish-next help setup test lint format format-check typecheck check prototype import-content render-assets validate-assets launch-review stage-media caption-review instagram-login instagram-auth-bootstrap instagram-auth-status instagram-logout

help:
	@echo "make render-next   Prepare the next queued Post with its publication colour"
	@echo "make publish-next  Preview next post; CONFIRM=1 requires interactive approval"
	@echo "make instagram-login Connect Instagram using browser login and local HTTPS"
	@echo "make instagram-auth-bootstrap Store the Instagram App Secret in macOS Keychain"
	@echo "make instagram-auth-status Inspect safe local credential metadata"
	@echo "make instagram-logout Remove local Instagram credential and App Secret"
	@echo "make caption-review Audit captions across 100 queues; write local review CSV"
	@echo "make launch-review  Generate the curated local launch QA gallery"
	@echo "make stage-media POST_ID=AED-0001 Temporarily expose one JPEG over HTTPS"
	@echo "make validate-assets Check strict PNG/JPEG inventory without writes"
	@echo "make render-assets Render SQLite Posts to PNG/JPEG using aed.toml"
	@echo "make import-content Import approved CSV content using aed.toml"
	@echo "make prototype     Render the Classic SVG design specimen"
	@echo "make setup         Install dependencies into .venv"
	@echo "make test          Run tests"
	@echo "make lint          Run Ruff lint checks"
	@echo "make format        Format Python files with Ruff"
	@echo "make format-check  Check formatting without changing files"
	@echo "make typecheck     Run Pyright"
	@echo "make check         Run all development checks"

setup:
	$(CAIRO_RUN) uv sync

test:
	$(CAIRO_RUN) uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run pyright

check: test lint format-check typecheck

prototype:
	uv run python -m ask_every_day.classic_prototype

import-content:
	uv run python -m ask_every_day.content_import

render-assets:
	$(CAIRO_RUN) uv run python -m ask_every_day.batch_render

validate-assets:
	uv run python -m ask_every_day.asset_inventory

launch-review:
	$(CAIRO_RUN) uv run python -m ask_every_day.launch_review

stage-media:
	uv run python -m ask_every_day.media_staging "$(POST_ID)"

caption-review:
	uv run python -m ask_every_day.caption_review

instagram-login:
	uv run python -m ask_every_day.instagram_auth_cli login

instagram-auth-bootstrap:
	uv run python -m ask_every_day.instagram_auth_cli bootstrap

instagram-auth-status:
	uv run python -m ask_every_day.instagram_auth_cli status

instagram-logout:
	uv run python -m ask_every_day.instagram_auth_cli logout

publish-next:
	uv run python -m ask_every_day.publish_next_cli $(if $(filter 1,$(CONFIRM)),--confirm,)

render-next:
	$(CAIRO_RUN) uv run python -m ask_every_day.render_next
