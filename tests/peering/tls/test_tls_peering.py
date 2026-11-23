"""
Prove that two INN nodes peer over mutual TLS, with stunnel sidecars.

Skipped by default. Run it with:
    uv run pytest tests/peering/tls -m peering -v

What is proven:
  1. an article posted on one node arrives on the other, in both directions;
  2. the traffic between the sidecars is encrypted, while the leg between innfeed
     and its own sidecar is still plain (so the capture is known to work);
  3. a third party is refused: with a certificate nobody trusts, with no certificate,
     and with plain NNTP on the TLS port;
  4. the same probe with a trusted certificate is let in (control for 3).
"""

import re
import uuid

import pytest

from tests.peering.nodes import wait_for

from .helpers import TLS_PORT, TlsNodes

pytestmark = [
    pytest.mark.peering,
    pytest.mark.thread_unsafe,  # all tests share the same two nodes
    pytest.mark.iterations(1),
]

ARRIVAL_TIMEOUT = 90
# innd greets with 200 (posting allowed) or 201 (not allowed) when it accepts.
GREETING = re.compile(r"^20[01] ", re.MULTILINE)


def wait_for_article(
    nodes: TlsNodes, sender: str, receiver: str, message_id: str
) -> str:
    token = wait_for(lambda: nodes.find(receiver, message_id), ARRIVAL_TIMEOUT)
    if token is None:
        pytest.fail(
            f"Article {message_id} posted on {sender} did not arrive on {receiver} "
            f"within {ARRIVAL_TIMEOUT} s.\n\n{nodes.sidecar_diagnostics()}"
        )
    return token


@pytest.mark.parametrize(
    ("sender", "receiver"),
    [("node-a", "node-b"), ("node-b", "node-a")],
    ids=["a_to_b", "b_to_a"],
)
def test_article_reaches_peer_over_tls(
    tls_nodes: TlsNodes, sender: str, receiver: str
) -> None:
    message_id = f"<tls-{uuid.uuid4().hex}@{sender}.invalid>"
    tls_nodes.post(sender, message_id, f"TLS round trip {sender} to {receiver}")

    token = wait_for_article(tls_nodes, sender, receiver, message_id)

    # Path is read right to left: posted on the sender, then stored on the receiver.
    path = tls_nodes.header(receiver, token, "Path")
    assert path.startswith(f"{receiver}!{sender}!"), (
        f"Article {message_id} is on {receiver}, but not via {sender}: Path: {path}"
    )


def test_traffic_between_sidecars_is_encrypted(tls_nodes: TlsNodes) -> None:
    """Capture on tls-a while an article goes from node-a to node-b."""
    marker = f"TLSPROOF-{uuid.uuid4().hex}"
    message_id = f"<tls-sniff-{uuid.uuid4().hex}@node-a.invalid>"

    # Two captures, killed by timeout. Port 11901: innfeed to its sidecar (plain).
    # Port 5119: sidecar to sidecar (TLS).
    tls_nodes.run_in(
        "sniffer",
        "rm -f /tmp/plain.txt /tmp/tls.txt; "
        "nohup timeout 60 tcpdump -l -A -n -i eth0 'tcp port 11901' "
        ">/tmp/plain.txt 2>&1 & "
        f"nohup timeout 60 tcpdump -l -A -n -i eth0 'tcp port {TLS_PORT}' "
        ">/tmp/tls.txt 2>&1 & "
        "sleep 3",
    )
    tls_nodes.post("node-a", message_id, marker)
    wait_for_article(tls_nodes, "node-a", "node-b", message_id)
    tls_nodes.run_in("sniffer", "sleep 2")  # let tcpdump write the last packets

    plain = tls_nodes.run_in("sniffer", "cat /tmp/plain.txt")
    encrypted = tls_nodes.run_in("sniffer", "cat /tmp/tls.txt")

    assert marker in plain, (
        "The capture of the plain leg does not hold the article: the capture itself "
        f"does not work, so the result for the TLS leg proves nothing.\n{plain[-1500:]}"
    )
    assert f".{TLS_PORT}" in encrypted, (
        f"No packets on the TLS leg.\n{encrypted[-1500:]}"
    )
    assert marker not in encrypted, (
        "The article is readable on the leg between the sidecars."
    )


def test_trusted_certificate_is_let_in(tls_nodes: TlsNodes) -> None:
    """Control for the refusals below: the same probe, with the certificate of tls-a."""
    answer = tls_nodes.tls_probe("-cert /certs/tls-a.crt -key /certs/tls-a.key")
    assert GREETING.search(answer), f"A trusted certificate was not let in:\n{answer}"


def test_untrusted_certificate_is_refused(tls_nodes: TlsNodes) -> None:
    answer = tls_nodes.tls_probe("-cert /certs/intruder.crt -key /certs/intruder.key")
    assert not GREETING.search(answer), (
        f"A certificate nobody trusts was let in:\n{answer}"
    )
    assert "alert" in answer, f"Expected a TLS alert from the sidecar:\n{answer}"


def test_no_certificate_is_refused(tls_nodes: TlsNodes) -> None:
    answer = tls_nodes.tls_probe()
    assert not GREETING.search(answer), (
        f"A client without certificate was let in:\n{answer}"
    )
    assert "alert" in answer, f"Expected a TLS alert from the sidecar:\n{answer}"


def test_plain_nntp_on_the_tls_port_is_refused(tls_nodes: TlsNodes) -> None:
    answer = tls_nodes.run_in(
        "intruder",
        f"printf 'DATE\\r\\nQUIT\\r\\n' | nc -w 5 tls-b {TLS_PORT}",
        timeout=30,
    )
    assert not GREETING.search(answer), (
        f"Plain NNTP got through the TLS port:\n{answer}"
    )
