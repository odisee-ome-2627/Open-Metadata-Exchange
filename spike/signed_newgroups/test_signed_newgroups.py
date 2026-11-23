# ruff: noqa: S101  # pytest checks with assert, as in tests/
"""
Spike: a library creates its newsgroups on every peer with signed control articles.

Two INN nodes (tests/peering) with the image of this folder. Each node trusts the
key of Odisee for ome.odisee.* and the key of HoGent for ome.hogent.*. Every test
posts a control article on node-a and checks what node-b does with it, and why
(INN's own syslog lines).

Run it from the root of the repository:
    uv run pytest spike/signed_newgroups -m peering -v
"""

import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from .ome_control import Signer, checkgroups, newgroup, rmgroup

REPO_ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
COMPOSE = [
    "-p", "ome-spike-signed-newgroups",
    "-f", str(REPO_ROOT / "tests" / "peering" / "compose.yaml"),
    "-f", str(HERE / "compose.yaml"),
]  # fmt: skip
INN_BIN = "/usr/lib/news/bin"
WAIT = 30  # seconds for a control article to reach node-b and be done
DOCKER = shutil.which("docker")

pytestmark = [
    pytest.mark.peering,
    pytest.mark.thread_unsafe,
    pytest.mark.iterations(1),
    pytest.mark.skipif(
        DOCKER is None or shutil.which("gpg") is None, reason="needs Docker and gpg"
    ),
]

# Which key may create groups where: the core of the proposal.
TRUST = {
    "ome-control-odisee": "ome.odisee.*",
    "ome-control-hogent": "ome.hogent.*",
}


def run(*args: str, stdin: str | None = None, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        args, input=stdin, capture_output=True, text=True, check=False, timeout=600
    )
    if check and result.returncode != 0:
        pytest.fail(f"{' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


@dataclass(frozen=True)
class Nodes:
    def exec(self, node: str, *cmd: str, stdin: str | None = None, user: str = "news",
             check: bool = True) -> str:  # fmt: skip
        return run(DOCKER, "compose", *COMPOSE, "exec", "-T", "-u", user, node, *cmd,
                   stdin=stdin, check=check)  # fmt: skip

    def inject(self, node: str, article: str) -> None:
        """Hand an article to innd like a peer (rnews): how an admin posts a control."""
        self.exec(node, f"{INN_BIN}/rnews", stdin=article)

    def has_group(self, node: str, group: str) -> bool:
        active = self.exec(node, "cat", "/var/lib/news/active")
        return any(line.split()[0] == group for line in active.splitlines() if line)

    def description(self, node: str, group: str) -> str:
        for line in self.exec(node, "cat", "/var/lib/news/newsgroups").splitlines():
            name, _, text = line.partition("\t")
            if name == group:
                return text.strip()
        return ""

    def syslog(self, node: str) -> str:
        return self.exec(node, "cat", "/var/log/news/syslog", check=False)

    def history(self, node: str, message_id: str) -> str:
        """The storage token, "/dev/null" for a remembered refusal, or ""."""
        return self.exec(
            node, f"{INN_BIN}/grephistory", message_id, check=False
        ).strip()

    def has_article(self, node: str, message_id: str) -> bool:
        return self.history(node, message_id).startswith("@")

    def post(self, node: str, group: str, message_id: str) -> None:
        article = (
            f"Newsgroups: {group}\nFrom: spike@{node}.invalid\nSubject: spike\n"
            f"Message-ID: {message_id}\n\nPosted on {node}.\n"
        )
        run(DOCKER, "compose", *COMPOSE, "exec", "-T", "-e", "NNTPSERVER=localhost",
            node, "inews", "-h", stdin=article)  # fmt: skip


def message_id_of(article: str) -> str:
    return next(
        line.split(" ", 1)[1]
        for line in article.splitlines()
        if line.startswith("Message-ID:")
    )


def refused(nodes: "Nodes", node: str, article: str) -> bool:
    """controlchan logged that pgpverify refused this article (its Message-ID)."""
    message_id = message_id_of(article)
    return any(
        "pgpverify failed" in line and message_id in line
        for line in nodes.syslog(node).splitlines()
    )


def wait_until(check: Callable[[], bool], timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(1)
    return check()


def group_name(library: str) -> str:
    return f"ome.{library}.spike{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def signers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Signer]:
    keys = tmp_path_factory.mktemp("keys")
    result = {}
    for library in ("odisee", "hogent", "mallory"):
        signer = Signer(
            keys / library, f"ome-control-{library}", library.capitalize(),
            f"ome@{library}.invalid", f"{library}.invalid",
        )  # fmt: skip
        signer.make_key()
        result[library] = signer
    return result


@pytest.fixture(scope="module")
def nodes(signers: dict[str, Signer]) -> Iterator[Nodes]:
    run(DOCKER, "build", "-q", "-f", str(REPO_ROOT / "Dockerfile-nntp"),
        "-t", "ome-nntp:peering-test", str(REPO_ROOT))  # fmt: skip
    run(DOCKER, "compose", *COMPOSE, "up", "--detach", "--build")
    nodes = Nodes()
    try:
        rules = "".join(
            f"{action}:*:{pattern}:verify-{user_id}\n"
            for user_id, pattern in TRUST.items()
            for action in ("newgroup", "checkgroups")
        )  # rmgroup keeps INN's default: mail the admin, change nothing
        for node in ("node-a", "node-b"):
            assert wait_until(
                lambda n=node: (
                    "Server running" in nodes.exec(n, "ctlinnd", "mode", check=False)
                ),
                60,
            )
            run(DOCKER, "compose", *COMPOSE, "exec", "-d", "-u", "root", node,
                "python3", "/usr/local/bin/syslog_to_file.py")  # fmt: skip
            # The trusted public keys (not mallory's): only root can change them.
            for library in ("odisee", "hogent"):
                key = signers[library].public_key()
                nodes.exec(node, "gpg1", "--homedir", "/etc/news/pgp", "--import",
                           stdin=key, user="root")  # fmt: skip
            nodes.exec(node, "chmod", "0644", "/etc/news/pgp/pubring.gpg", user="root")
            nodes.exec(
                node,
                "sh",
                "-c",
                "cat >> /etc/news/control.ctl.local",
                stdin=rules,
                user="root",
            )
        yield nodes
    finally:
        run(
            DOCKER,
            "compose",
            *COMPOSE,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
        )


def test_signed_newgroup_reaches_every_peer(
    nodes: Nodes, signers: dict[str, Signer]
) -> None:
    """Odisee signs a newgroup in its namespace: every node creates it, named."""
    group = group_name("odisee")
    nodes.inject(
        "node-a", newgroup(signers["odisee"], group, "Odisee library: test channel")
    )
    assert wait_until(lambda: nodes.has_group("node-b", group)), nodes.syslog("node-b")
    assert nodes.has_group("node-a", group)
    assert nodes.description("node-b", group) == "Odisee library: test channel"


def test_unsigned_newgroup_is_ignored(nodes: Nodes, signers: dict[str, Signer]) -> None:
    """Without a signature the namespace rule still applies: pgpverify refuses it."""
    group = group_name("odisee")
    signed = newgroup(signers["odisee"], group, "unsigned")
    head, body = signed.split("\n\n", 1)
    unsigned = (
        "\n".join(
            line
            for line in head.splitlines()
            if not line.startswith(("X-PGP-Sig", "\t"))
        )
        + "\n\n"
        + body
    )
    nodes.inject("node-a", unsigned)
    assert wait_until(lambda: refused(nodes, "node-b", unsigned)), nodes.syslog(
        "node-b"
    )
    assert not nodes.has_group("node-b", group)


@pytest.mark.parametrize(
    ("library", "namespace"),
    [("odisee", "hogent"), ("mallory", "odisee")],
    ids=["key_of_another_library", "unknown_key"],
)
def test_wrong_key_is_refused(nodes: Nodes, signers: dict[str, Signer], library: str,
                              namespace: str) -> None:  # fmt: skip
    """Odisee cannot create a HoGent group, and a key nobody trusts creates nothing."""
    group = group_name(namespace)
    article = newgroup(signers[library], group, "not allowed")
    nodes.inject("node-a", article)
    assert wait_until(lambda: refused(nodes, "node-b", article)), nodes.syslog("node-b")
    assert not nodes.has_group("node-b", group)


def test_changed_article_is_refused(nodes: Nodes, signers: dict[str, Signer]) -> None:
    """Changing a signed header after signing (here the group) breaks the signature."""
    group = group_name("odisee")
    other = group_name("odisee")
    article = newgroup(signers["odisee"], group, "original").replace(group, other)
    nodes.inject("node-a", article)
    assert wait_until(lambda: refused(nodes, "node-b", article)), nodes.syslog("node-b")
    assert not nodes.has_group("node-b", other)


def test_article_before_its_group_is_lost(
    nodes: Nodes, signers: dict[str, Signer]
) -> None:
    """An article that arrives before its group is refused, and not sent again."""
    group = group_name("odisee")
    nodes.exec(
        "node-a", "ctlinnd", "newgroup", group
    )  # only on node-a, no control article
    message_id = f"<early-{uuid.uuid4().hex}@node-a.invalid>"
    nodes.post("node-a", group, message_id)
    # node-b refused it, and remembers that (remembertrash): history says /dev/null.
    assert wait_until(lambda: nodes.history("node-b", message_id) == "/dev/null")
    assert not nodes.has_article("node-b", message_id)

    nodes.inject("node-a", newgroup(signers["odisee"], group, "created too late"))
    assert wait_until(lambda: nodes.has_group("node-b", group))
    time.sleep(WAIT)
    assert not nodes.has_article("node-b", message_id), (
        "the early article came after all"
    )


def test_checkgroups_brings_missing_groups(
    nodes: Nodes, signers: dict[str, Signer]
) -> None:
    """A node that missed a newgroup gets it from Odisee's signed list of groups."""
    missed = group_name("odisee")
    nodes.exec("node-a", "ctlinnd", "newgroup", missed)  # node-b never heard of it
    assert not nodes.has_group("node-b", missed)
    active = nodes.exec("node-a", "cat", "/var/lib/news/active").splitlines()
    groups = {
        line.split()[0]: "Odisee group"
        for line in active
        if line.startswith("ome.odisee.")
    }
    article = checkgroups(signers["odisee"], "ome.odisee", groups, admin_group=missed)
    nodes.inject("node-a", article)
    assert wait_until(lambda: nodes.has_group("node-b", missed)), nodes.syslog("node-b")


def test_signed_rmgroup_only_mails_the_admin(
    nodes: Nodes, signers: dict[str, Signer]
) -> None:
    """Removing a group is never automatic, not even with a valid signature."""
    group = group_name("odisee")
    nodes.inject("node-a", newgroup(signers["odisee"], group, "to keep"))
    assert wait_until(lambda: nodes.has_group("node-b", group))
    nodes.inject("node-a", rmgroup(signers["odisee"], group))
    assert wait_until(
        lambda: (
            f"rmgroup {group}" in nodes.syslog("node-b")
            or "mail" in nodes.syslog("node-b")
        )
    )
    time.sleep(5)
    assert nodes.has_group("node-b", group)


def test_articles_are_not_protected(nodes: Nodes, signers: dict[str, Signer]) -> None:
    """A limit of the proposal: any peer can post in another library's group."""
    group = group_name("odisee")
    nodes.inject("node-a", newgroup(signers["odisee"], group, "Odisee only?"))
    assert wait_until(lambda: nodes.has_group("node-b", group))
    message_id = f"<not-odisee-{uuid.uuid4().hex}@node-b.invalid>"
    nodes.post("node-b", group, message_id)  # node-b is not Odisee
    assert wait_until(lambda: nodes.has_article("node-a", message_id))
