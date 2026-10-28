"""pytest plugin: `pytest_plugins = ["aws_mock_harness.plugin"]` in a conftest gives tests an isolated local AWS mock."""

from __future__ import annotations

import pytest

from . import helpers
from .guard import forbid_real_aws
from .server import MockAws


@pytest.fixture(scope="session")
def _mock_server():
    with MockAws() as m:
        yield m


@pytest.fixture(autouse=True)
def _no_real_aws():
    with forbid_real_aws():
        yield


@pytest.fixture()
def aws(_mock_server):
    """The shared server with all state wiped before AND after the test (no leakage in either direction)."""
    _mock_server.reset()
    yield _mock_server
    _mock_server.reset()


@pytest.fixture()
def bucket(aws):
    return helpers.make_bucket(aws, "test-bucket")


@pytest.fixture()
def queue(aws):
    return helpers.make_queue(aws, "test-queue")
