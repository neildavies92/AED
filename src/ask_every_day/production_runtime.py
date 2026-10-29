"""Shared fail-closed boundary for every production media publication path."""

import sys
from pathlib import Path


class DockerPublisherRequired(ValueError):
    """A fixed diagnostic; never includes configuration or credential material."""


def docker_runtime() -> bool:
    return sys.platform == "linux" and Path("/.dockerenv").is_file()


def require_docker_publisher() -> None:
    if not docker_runtime():
        raise DockerPublisherRequired("docker_publisher_required")
