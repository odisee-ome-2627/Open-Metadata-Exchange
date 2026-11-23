"""
Helpers for the TLS peering test: the Docker Compose project in compose.yaml.

TlsNodes reuses the helpers of the plain peering test (post, find, header, ...) and only
points them at the TLS compose file. compose() sends input as bytes: in text mode Python
on Windows turns "\\n" into "\\r\\n", and inews then refuses the article.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.peering.nodes import PeeringNodes

TLS_COMPOSE_FILE = Path(__file__).parent / "compose.yaml"
SIDECARS = ("tls-a", "tls-b")
TLS_PORT = 5119  # the port of the sidecars, between the nodes (see stunnel-*.conf)


def _text(output: bytes | str | None) -> str:
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output or ""


@dataclass(frozen=True)
class TlsNodes(PeeringNodes):
    """The two INN nodes with their stunnel sidecars, plus an intruder and a sniffer."""

    def compose(
        self,
        *args: str,
        input_text: str | None = None,
        check: bool = True,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.docker, "compose", "-f", str(TLS_COMPOSE_FILE), *args]
        try:
            raw = subprocess.run(  # noqa: S603 - fixed arguments, no shell
                command,
                input=input_text.encode() if input_text is not None else None,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            result = subprocess.CompletedProcess(
                command, raw.returncode, _text(raw.stdout), _text(raw.stderr)
            )
        except subprocess.TimeoutExpired as error:
            result = subprocess.CompletedProcess(
                command,
                returncode=124,  # exit code of timeout(1)
                stdout=_text(error.stdout),
                stderr=f"{_text(error.stderr)}Timed out after {timeout} s\n",
            )
        if check and result.returncode != 0:
            pytest.fail(
                f"Command failed with exit code {result.returncode}: "
                f"{' '.join(command)}\n{result.stdout}{result.stderr}"
            )
        return result

    def run_in(self, service: str, script: str, timeout: float = 60) -> str:
        """Run a shell script in a service (intruder, sniffer, ...): stdout + stderr."""
        result = self.compose(
            "exec", "-T", service, "sh", "-c", script, check=False, timeout=timeout
        )
        return result.stdout + result.stderr

    def tls_probe(
        self, client_args: str = "", payload: str = "DATE\\r\\nQUIT\\r\\n"
    ) -> str:
        """Open a TLS connection to the sidecar of node-b from the intruder container.

        client_args are extra openssl s_client options, normally -cert and -key.
        Returns what came back: the NNTP greeting of INN if the sidecar let us in,
        otherwise only an error from the handshake.
        """
        return self.run_in(
            "intruder",
            f"printf '{payload}' | timeout 15 openssl s_client -quiet -tls1_2 "
            f"-connect tls-b:{TLS_PORT} -CAfile /certs/tls-b.crt {client_args} 2>&1",
            timeout=30,
        )

    def sidecar_diagnostics(self) -> str:
        """Sidecar logs and innfeed logs of both nodes, for a failure message."""
        parts = [self.diagnostics("node-a", "node-b")]
        for sidecar in SIDECARS:
            logs = self.compose(
                "logs", "--no-color", "--tail", "30", sidecar, check=False
            )
            parts.append(
                f"===== {sidecar}: stunnel log =====\n{logs.stdout}{logs.stderr}"
            )
        return "\n".join(parts)
