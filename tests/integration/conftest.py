"""Shared fixtures for tests that run a real sandbox container."""

from collections.abc import Iterator

import pytest

from patchpilot.sandbox import DockerSandbox


@pytest.fixture(scope="session")
def docker_sandbox() -> DockerSandbox:
    sandbox = DockerSandbox()
    if not sandbox.is_available():
        pytest.skip("no Docker engine is reachable")
    sandbox.ensure_image()
    return sandbox


@pytest.fixture
def sandbox(docker_sandbox: DockerSandbox) -> Iterator[DockerSandbox]:
    """Give a test the sandbox and check that it leaves no container behind."""
    assert docker_sandbox.list_containers() == [], "a sandbox container existed before the test"
    yield docker_sandbox
    leftovers = docker_sandbox.remove_leftovers()
    assert leftovers == [], f"the test left sandbox containers behind: {leftovers}"
