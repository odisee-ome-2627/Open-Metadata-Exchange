"""
Fixture for the TLS peering test: two INN nodes with stunnel sidecars.

Started once per test session and always removed again, also when a test fails.
"""

import shutil
from collections.abc import Iterator

import pytest

from tests.peering.nodes import BUILD_TIMEOUT, NEWSGROUP, NODES

from .helpers import TlsNodes


@pytest.fixture(scope="session")
def tls_nodes() -> Iterator[TlsNodes]:
    """Start the TLS setup, wait until INN is ready, create the test group, clean up."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("The TLS peering test needs Docker with Docker Compose v2 on PATH.")
    nodes = TlsNodes(docker)
    try:
        nodes.compose("up", "--detach", "--build", timeout=BUILD_TIMEOUT)
        for node in NODES:
            nodes.wait_ready(node)
            # A node only stores articles in groups it knows.
            nodes.exec(node, "ctlinnd", "newgroup", NEWSGROUP)
        yield nodes
    finally:
        # --volumes: the only volume of this project holds the throwaway certificates.
        nodes.compose("down", "--remove-orphans", "--volumes", check=False)
