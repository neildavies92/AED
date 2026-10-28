.DEFAULT_GOAL := help
.PHONY: help inspect export verify restore monitor visual-review
ARGS ?=
help:
	@echo 'Deployment development helpers; no host setup, cron installation or activation.'
	@echo 'make -f deployment/Makefile inspect|export|verify|restore|monitor|visual-review ARGS="..."'
inspect export verify restore monitor:
	uv run python -m ask_every_day.deployment $@ $(ARGS)

visual-review:
	sh scripts/with-cairo.sh uv run python deployment/visual_review.py $(ARGS)

.PHONY: desktop-prepare
desktop-prepare:
	uv run python deployment/desktop.py $(ARGS)

.PHONY: desktop-run
desktop-run:
	uv run python deployment/desktop-compose.py $(ARGS)
