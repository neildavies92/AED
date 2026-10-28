.DEFAULT_GOAL := help
IMAGE ?= ask-every-day:local
TEST_IMAGE ?= ask-every-day:test
REVISION ?= $(shell git -C $(ROOT) rev-parse HEAD)
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
.PHONY: help build test smoke audit preview status
help:
	@echo "make -f container/Makefile build   Build the pinned non-root runtime"
	@echo "make -f container/Makefile test    Build/run Linux tests without network"
	@echo "make -f container/Makefile smoke   Run nonpublishing persistence/render smoke"
	@echo "make -f container/Makefile preview Inspect operator-mounted state offline"
build:
	docker build --build-arg AED_REVISION=$(REVISION) --target runtime -t $(IMAGE) $(ROOT)
test:
	docker build --build-arg AED_REVISION=$(REVISION) --target test -t $(TEST_IMAGE) $(ROOT)
	docker run --rm --network none --read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=128m,mode=1777 $(TEST_IMAGE) -p no:cacheprovider -q tests/test_renderer_regression.py tests/test_file_credential_store.py tests/test_container_runtime.py tests/test_media_staging.py tests/test_scheduler.py tests/test_scheduler_cli.py tests/test_font_review.py tests/test_desktop_validation.py tests/test_deployment.py tests/test_deployment_trigger.py tests/test_credential_handoff.py tests/test_publication_recovery_cli.py tests/test_canonical_manual.py tests/test_publish_next.py tests/test_publication_assets.py
audit:
	python3 $(ROOT)/container/audit.py
smoke:
	python3 $(ROOT)/container/smoke.py --image $(IMAGE)
preview:
	docker compose -f $(ROOT)/container/compose.yaml run --rm --no-deps publisher preview
status:
	docker compose -f $(ROOT)/container/compose.yaml run --rm --no-deps publisher publication-status
