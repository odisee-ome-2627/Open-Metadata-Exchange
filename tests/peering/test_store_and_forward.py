"""
Prove store and forward: articles posted while a peer is down reach it later,
from innfeed's backlog, each exactly once.

Skipped by default. Run it with:
    uv run pytest tests/peering -m peering -v
"""

import uuid

import pytest

from .nodes import NEWSGROUP, PeeringNodes, wait_for

pytestmark = [
    pytest.mark.peering,
    pytest.mark.thread_unsafe,  # stops node-b, which the other peering tests use
    pytest.mark.iterations(1),
]

SENDER = "node-a"
RECEIVER = "node-b"
# More than one, so a lost or a twice delivered article shows up in the count.
ARTICLES = 3
# tradspool keeps one file per article, in a folder per newsgroup.
GROUP_FOLDER = "/var/spool/news/articles/" + NEWSGROUP.replace(".", "/")
INNFEED_LOG = "/var/log/news/innfeed.log"
ARRIVAL_TIMEOUT = 60  # seconds after node-b is back; innfeed retries at most every 10 s


def article_count(nodes: PeeringNodes, node: str) -> int:
    """Number of articles in the test group on this node."""
    result = nodes.exec(node, "sh", "-c", f"ls {GROUP_FOLDER} 2>/dev/null | wc -l")
    return int(result.stdout.strip())


def log_count(nodes: PeeringNodes, node: str, text: str) -> int:
    """How often innfeed on this node logged this exact text."""
    result = nodes.exec(node, "grep", "-c", "-F", text, INNFEED_LOG, check=False)
    return int(result.stdout.strip() or 0)


def test_articles_reach_peer_after_outage(peering_nodes: PeeringNodes) -> None:
    nodes = peering_nodes
    before = article_count(nodes, RECEIVER)
    message_ids = [
        f"<backlog-{uuid.uuid4().hex}@{SENDER}.invalid>" for _ in range(ARTICLES)
    ]

    # "stop" returns once node-b is down, so its connection is closed: innfeed cannot
    # reach node-b and must keep the articles in its backlog.
    nodes.stop(RECEIVER)
    try:
        for message_id in message_ids:
            nodes.post(SENDER, message_id, "Posted while the peer is down")
            assert nodes.find(SENDER, message_id), (
                f"{message_id} not stored on {SENDER}"
            )
    finally:
        nodes.start(RECEIVER)  # also after a failure: the other tests need node-b

    for message_id in message_ids:
        token = wait_for(lambda m=message_id: nodes.find(RECEIVER, m), ARRIVAL_TIMEOUT)
        if token is None:
            pytest.fail(
                f"{message_id}, posted while {RECEIVER} was down, did not arrive "
                f"within {ARRIVAL_TIMEOUT} s after it came back.\n\n"
                f"{nodes.diagnostics(SENDER, RECEIVER)}"
            )
        path = nodes.header(RECEIVER, token, "Path")
        assert path.startswith(f"{RECEIVER}!{SENDER}!"), f"{message_id}: Path: {path}"
        # Proof that the article came from the backlog, not from an open connection.
        # This line needs debug-level 4 or higher in innfeed.conf.j2.
        assert log_count(nodes, SENDER, f"Peeled article {message_id} from backlog"), (
            f"{message_id} reached {RECEIVER}, but not from the backlog on {SENDER}"
        )

    after = article_count(nodes, RECEIVER)
    assert after == before + ARTICLES, (
        f"{RECEIVER} has {after - before} new articles in {NEWSGROUP}, "
        f"expected {ARTICLES}: an article was lost or delivered twice"
    )
