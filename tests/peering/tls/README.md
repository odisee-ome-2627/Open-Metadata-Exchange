# TLS peering test (proof of concept)

Two INN nodes peer with each other over **mutual TLS**, with a stunnel sidecar next to
each node. This is a proof of concept: it shows that NNTP peering can be encrypted and
authenticated without changing INN or the Jinja templates. It is not production-ready
(see "Limits" below).

```text
 node-a --plain--> tls-a ===== TLS, both sides show a certificate =====> tls-b --plain--> node-b
 172.31.0.11   :11901  172.31.0.21                     :5119           172.31.0.22          172.31.0.12
```

- innfeed of a node connects to its own sidecar (`tls-a:11901`, plain NNTP inside the Docker
  network). The sidecar forwards the traffic over TLS to the sidecar of the peer (port 5119).
- The receiving sidecar checks the client certificate, then hands the traffic to INN on port 119.
- Certificates are self-signed and pinned: each sidecar trusts exactly the certificate of the
  other one, nothing else. There is no CA.
- INN needs no change. In `node-*.json` the peer's `dns_name`, `port` and `incoming_ip` point at
  the node's **own sidecar**, because that is where the connections come from and go to.

## Run the test

Requirements: Docker with Docker Compose v2, and [uv](https://docs.astral.sh/uv/). From the root
of the repository:

```shell
uv run pytest tests/peering/tls -m peering -v
```

`uv run pytest tests/peering -m peering` runs this test and the plain peering test, each with
its own Docker network (172.31.0.0/24 and 172.30.0.0/24). The helpers here send input as bytes, so
these tests also work on Windows without changes to `tests/peering/nodes.py`.

## What the tests prove

| Test | Proves |
| ---- | ------ |
| `test_article_reaches_peer_over_tls` | An article arrives on the other node, both ways, via the sidecars. |
| `test_traffic_between_sidecars_is_encrypted` | tcpdump on `tls-a`: the article is readable on the plain leg (innfeed to sidecar) and not on the TLS leg (sidecar to sidecar). |
| `test_trusted_certificate_is_let_in` | Control: a client with the certificate of `tls-a` gets the INN greeting. |
| `test_untrusted_certificate_is_refused` | A self-signed certificate nobody trusts is refused (TLS alert). |
| `test_no_certificate_is_refused` | A client without a certificate is refused (TLS alert). |
| `test_plain_nntp_on_the_tls_port_is_refused` | Plain NNTP on the TLS port gets no answer from INN. |

## Files

| File | Purpose |
| ---- | ------- |
| `compose.yaml` | Nodes, sidecars, a one-shot `certgen`, an `intruder` and a `sniffer` for the tests |
| `Dockerfile` | Debian with stunnel4, openssl, netcat and tcpdump (one image for all helper services) |
| `gen-certs.sh` | Makes `tls-a`, `tls-b` and `intruder` certificates into the shared `certs` volume |
| `stunnel-a.conf`, `stunnel-b.conf` | One client service (`to-node-*`) and one server service (`from-node-*`) each |
| `node-a.json`, `node-b.json` | INN configs; the peer points at the node's own sidecar |
| `helpers.py`, `conftest.py`, `test_tls_peering.py` | `TlsNodes` helper, session fixture, tests |

## Limits (on purpose, for later)

- **Peer identity.** INN identifies a peer by source address. Behind the sidecar every inbound
  connection comes from the sidecar's own address, so INN can no longer tell peers apart. With one
  peer per node this works; with several peers all of them share one `incoming.conf` identity. The
  TLS layer does know who connected (the pinned certificate); a design for several peers needs
  one server service per peer, with its own source address.
- **Plain entry on the sidecar.** `tls-a:11901` accepts plain NNTP from everything in the same
  Docker network, and sends it on as `node-a`. In a real setup this port must only be reachable by
  the INN container (own internal network).
- **Certificates** are generated per test run and thrown away. Exchange, renewal and revocation in
  production are not solved here.
- **Readers** (nnrpd, the API) are out of scope; only peering is encrypted.
