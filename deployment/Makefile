.DEFAULT_GOAL := help
.PHONY: help inspect export verify restore monitor visual-review
ARGS ?=
help:
	@echo 'Deployment helpers; help/setup never install or activate a host trigger.'
	@echo 'make -f deployment/Makefile inspect|export|verify|restore|monitor|visual-review ARGS="..."'
	@echo 'make -f deployment/Makefile desktop-prepare|desktop-run ARGS="..."'
	@echo 'make -f deployment/Makefile desktop-scheduler ARGS="prepare|diagnose|install|activate|disable|uninstall --directory ..."'
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

.PHONY: desktop-scheduler
desktop-scheduler:
	uv run python deployment/desktop_scheduler.py $(ARGS)
