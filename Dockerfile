# Immutable multi-architecture vendor images; update through reviewed rebuilds.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM cloudflare/cloudflared:2026.10.1@sha256:a15a3b50b8dabd57ac2090e9484c0247dba9dfa1f0df4d298d4317f9d69eb2d5 AS tunnel
FROM python:3.13-slim-trixie@sha256:b3575dcf816cdacc5292df77a6e3e59b8941631078373a045efa2ac1e9c5c3b2 AS runtime

# Snapshot apt metadata as well as the base image and uv.lock.
RUN rm /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
       'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261027T000000Z trixie main' \
       'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20261027T000000Z trixie-security main' \
       > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libcairo2 libffi8 libfontconfig1 fontconfig fonts-liberation2 tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=tunnel /usr/local/bin/cloudflared /usr/local/bin/cloudflared
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/aed/.venv/bin:/usr/local/bin:/usr/bin:/bin" HOME=/tmp/aed-home
WORKDIR /opt/aed
COPY pyproject.toml uv.lock README.md ./
COPY container/build-requirements.txt ./container/build-requirements.txt
COPY src ./src
# Build in a separate hash-pinned environment; do not resolve floating PEP517 tools.
RUN uv venv /tmp/aed-builder \
    && uv pip install --python /tmp/aed-builder/bin/python --require-hashes -r container/build-requirements.txt \
    && uv build --python /tmp/aed-builder/bin/python --no-build-isolation --wheel --out-dir /opt/aed/wheel \
    && uv sync --locked --no-dev --no-editable --no-cache --no-install-project \
    && uv pip install --python .venv/bin/python --no-deps /opt/aed/wheel/*.whl \
    && rm -rf /tmp/aed-builder /root/.cache/uv \
    && /usr/sbin/groupadd --gid 10001 aed \
    && /usr/sbin/useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin aed \
    && mkdir -p /var/lib/aed /var/lib/aed-credentials /etc/aed \
    && chown aed:aed /var/lib/aed /var/lib/aed-credentials \
    && chmod 700 /var/lib/aed-credentials
COPY data/prompts.csv data/taxonomy.csv data/hashtags.csv data/captions.csv data/caption_profiles.csv ./data/
ARG AED_REVISION=unversioned
LABEL org.opencontainers.image.revision=$AED_REVISION
USER 10001:10001
ENTRYPOINT ["python", "-m", "ask_every_day.container_runtime"]
CMD ["preview"]

# Test target is never the deployed runtime image; no host .venv/credentials copied.
FROM runtime AS test
USER root
COPY tests ./tests
COPY deployment/trigger.py ./deployment/trigger.py
COPY deployment/desktop.py ./deployment/desktop.py
COPY Dockerfile .dockerignore ./
COPY container/compose.yaml ./container/compose.yaml
COPY scripts/with-cairo.sh ./scripts/with-cairo.sh
RUN apt-get update \
    && apt-get install --no-install-recommends -y procps \
    && rm -rf /var/lib/apt/lists/* \
    && uv sync --locked --no-editable --no-cache --no-install-project \
    && uv pip install --python .venv/bin/python --no-deps /opt/aed/wheel/*.whl \
    && chmod -R a+rX /opt/aed
USER 10001:10001
ENTRYPOINT ["/opt/aed/.venv/bin/python", "-m", "pytest"]
CMD ["tests/test_renderer_regression.py", "tests/test_file_credential_store.py", "tests/test_media_staging.py", "tests/test_container_runtime.py", "-q"]

# Plain docker build selects the safe deployable image, not the test stage.
FROM runtime AS production
