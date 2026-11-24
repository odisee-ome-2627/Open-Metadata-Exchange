"""
INN's own messages reach `docker logs` (#294).

innd, innfeed and nnrpd log through syslog. The image starts a small syslog daemon
that writes every message to the output of the container, so an operator sees why
a node stops.

Skipped by default. Run it with:
    uv run pytest tests/peering -m peering -v
"""

import json
import subprocess
from pathlib import Path

import pytest

from .nodes import PeeringNodes

pytestmark = [
    pytest.mark.peering,
    pytest.mark.thread_unsafe,  # uses the nodes of the session
    pytest.mark.iterations(1),
]

NODE_A_CONFIG = Path(__file__).parent / "node-a.json"
IMAGE = "ome-nntp:peering-test"  # Built by the fixture, see compose.yaml
WAIT_FOR_INND = (
    "for i in $(seq 60); do"
    " ctlinnd mode 2>/dev/null | grep -q 'Server running' && exit 0; sleep 1;"
    " done; exit 1"
)


def run_node(docker: str, config_file: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [docker, *args, "-v", f"{config_file}:/run/secrets/ome_config:ro", IMAGE],
        capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip


def test_innd_messages_are_in_docker_logs(peering_nodes: PeeringNodes) -> None:
    """A running node shows innd's syslog messages, with the program name."""
    logs = peering_nodes.compose("logs", "--no-color", "node-a", check=False)
    assert "innd: SERVER" in logs.stdout, logs.stdout[-2000:]


def test_broken_config_says_why(peering_nodes: PeeringNodes, tmp_path: Path) -> None:
    """A node with a broken peer name stops, and the logs name the file innd refuses."""
    config = json.loads(NODE_A_CONFIG.read_text())
    config["peers"][0]["name"] = "node b:typo"  # A space and a colon break newsfeeds
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))
    result = run_node(peering_nodes.docker, config_file, "run", "--rm")
    output = result.stdout + result.stderr
    assert result.returncode == 1, output[-2000:]
    reasons = [line for line in output.splitlines() if "innd:" in line]
    assert any("/etc/news/newsfeeds" in line for line in reasons), output[-2000:]


def test_shutdown_messages_are_in_docker_logs(
    peering_nodes: PeeringNodes, tmp_path: Path
) -> None:
    """syslogd stops last: innfeed's goodbye after a clean stop is in the logs."""
    config_file = tmp_path / "config.json"
    config_file.write_text(NODE_A_CONFIG.read_text())
    started = run_node(peering_nodes.docker, config_file, "run", "-d")
    container = started.stdout.strip()
    assert started.returncode == 0, started.stderr
    try:
        ready = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [peering_nodes.docker, "exec", container, "sh", "-c", WAIT_FOR_INND],
            capture_output=True, text=True, check=False, timeout=90,
        )  # fmt: skip
        assert ready.returncode == 0, "innd did not start"
        subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [peering_nodes.docker, "stop", "-t", "30", container],
            capture_output=True, check=True, timeout=60,
        )  # fmt: skip
        logs = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [peering_nodes.docker, "logs", container],
            capture_output=True, text=True, check=True, timeout=30,
        )  # fmt: skip
        output = logs.stdout + logs.stderr
        assert "innfeed" in output, output[-2000:]
        assert "finishing" in output, output[-2000:]
    finally:
        subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [peering_nodes.docker, "rm", "-f", container],
            capture_output=True, check=False, timeout=60,
        )  # fmt: skip
