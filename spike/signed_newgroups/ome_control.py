"""
Signed control articles for OME newsgroups: newgroup, rmgroup and checkgroups.

A library signs the control articles for its own newsgroups (for example
``ome.odisee.*``) with its PGP key. A peer that trusts that key for that namespace
creates the group on its own node (INN's controlchan with pgpverify, rule
``verify-<user ID>`` in control.ctl).

The format is the one INN's pgpverify checks, as made by Julien Élie's signcontrol
(https://github.com/Julien-Elie/usenet-signcontrol). Unlike signcontrol, this module
asks no questions, calls gpg without a shell, and never puts a passphrase on the
command line.

Spike, not product code: see README.md.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

# The headers that the signature covers, in the order pgpverify rebuilds them.
SIGNED_HEADERS = ("Subject", "Control", "Message-ID", "Date", "Injection-Date", "From")
BOUNDARY = "signcontrol"


class GpgError(RuntimeError):
    """GnuPG is missing or one of its commands failed."""


@dataclass(frozen=True)
class Signer:
    """The PGP key of one library, kept in its own GnuPG home folder."""

    gnupghome: Path
    user_id: str  # Peers trust it in control.ctl as verify-<user_id>
    name: str  # Shown in From
    mail: str  # Address in From and Approved
    host: str  # Right part of the Message-IDs

    def gpg(self, *args: str, stdin: str | None = None) -> str:
        gpg = shutil.which("gpg")
        if gpg is None:
            msg = "GnuPG (gpg) is not installed"
            raise GpgError(msg)
        result = subprocess.run(  # noqa: S603 - argument list, no shell
            [gpg, "--homedir", str(self.gnupghome), "--batch", "--yes", *args],
            input=stdin, capture_output=True, text=True, check=False,
        )  # fmt: skip
        if result.returncode != 0:
            msg = f"gpg {' '.join(args)} failed:\n{result.stderr}"
            raise GpgError(msg)
        return result.stdout

    def make_key(self, passphrase: str = "") -> None:
        """An RSA signing key: GnuPG 1.4, which INN uses to verify, has no Ed25519."""
        self.gnupghome.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.gpg(
            "--pinentry-mode", "loopback", "--passphrase-fd", "0",
            "--quick-generate-key", self.user_id, "rsa3072", "sign", "never",
            stdin=passphrase + "\n",
        )  # fmt: skip

    def public_key(self) -> str:
        """The public key in ASCII armor, to give to the peers."""
        return self.gpg("--armor", "--export", f"={self.user_id}")

    def sign(self, text: str, passphrase: str | None = None) -> tuple[str, list[str]]:
        """A detached signature of text: the version token and the base64 lines."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "article.txt"
            data.write_text(text)
            args = ["--emit-version", "--armor", "--detach-sign"]
            args += ["--local-user", f"={self.user_id}", "--output", "-"]
            stdin = None
            if passphrase is not None:
                args = ["--pinentry-mode", "loopback", "--passphrase-fd", "0", *args]
                stdin = passphrase + "\n"
            armor = self.gpg(*args, str(data), stdin=stdin)
        version = "GnuPG"
        lines = []
        for line in armor.splitlines():
            if line.startswith("Version:"):
                version = line.removeprefix("Version:").strip().replace(" ", "_")
            elif line and not line.startswith(("-----", "Comment:")):
                lines.append(line)
        return version, lines


@dataclass(frozen=True)
class Control:
    """The parts of a control article that differ per kind."""

    newsgroups: str
    subject: str
    control: str
    content_type: str
    body: str


def _now() -> str:
    return format_datetime(datetime.now(UTC).replace(microsecond=0))


def _normalize(text: str) -> str:
    """pgpverify strips spaces and tabs at the end of every line before it verifies."""
    return re.sub(r"[ \t]+\n", "\n", text)


def signed_article(
    signer: Signer, control: Control, passphrase: str | None = None
) -> str:
    """A complete control article, signed in pgpverify's format."""
    kind = control.control.split(maxsplit=1)[0]
    stamp = f"{int(time.time())}.{uuid.uuid4().hex[:8]}"
    now = _now()
    signed = {
        "Subject": control.subject,
        "Control": control.control,
        "Message-ID": f"<{kind}-{stamp}@{signer.host}>",
        "Date": now,
        "Injection-Date": now,
        "From": f"{signer.name} <{signer.mail}>",
    }
    body = _normalize(control.body)
    text = "X-Signed-Headers: " + ",".join(SIGNED_HEADERS) + "\n"
    text += "".join(f"{name}: {signed[name]}\n" for name in SIGNED_HEADERS)
    text += "\n" + body
    version, signature = signer.sign(text, passphrase)

    headers = [f"{name}: {signed[name]}" for name in SIGNED_HEADERS]
    headers += [
        f"Approved: {signer.mail}",
        f"Newsgroups: {control.newsgroups}",
        # Not signed: every server that relays the article adds itself to Path.
        "Path: not-for-mail",
        "MIME-Version: 1.0",
        f"Content-Type: {control.content_type}",
        "Content-Transfer-Encoding: 8bit",
        f"X-PGP-Sig: {version} {','.join(SIGNED_HEADERS)}",
    ]
    headers += [f"\t{line}" for line in signature]
    return "\n".join(headers) + "\n\n" + body


def newgroup(signer: Signer, group: str, description: str, **kw: str) -> str:
    """Create group on every peer that trusts this signer for it."""
    body = (
        "This is a MIME NetNews control message.\n"
        f"--{BOUNDARY}\n"
        "Content-Type: text/plain; charset=UTF-8\n\n"
        f"{group} is a newsgroup of {signer.name} on the Open Metadata Exchange.\n\n"
        f"--{BOUNDARY}\n"
        "Content-Type: application/news-groupinfo; charset=UTF-8\n\n"
        "For your newsgroups file:\n"
        f"{group}\t{description}\n\n"
        f"--{BOUNDARY}--\n"
    )
    control = Control(
        group, f"cmsg newgroup {group}", f"newgroup {group}",
        f'multipart/mixed; boundary="{BOUNDARY}"', body,
    )  # fmt: skip
    return signed_article(signer, control, **kw)


def rmgroup(signer: Signer, group: str, **kw: str) -> str:
    """Remove group on every peer that trusts this signer for it."""
    control = Control(
        group, f"cmsg rmgroup {group}", f"rmgroup {group}",
        "text/plain; charset=UTF-8", f"{group} is no longer used by {signer.name}.\n",
    )  # fmt: skip
    return signed_article(signer, control, **kw)


def checkgroups(
    signer: Signer, scope: str, groups: dict[str, str], admin_group: str, **kw: str
) -> str:
    """All groups in scope (such as ome.odisee), for nodes that missed one."""
    serial = int(time.time())
    body = "".join(f"{name}\t{text}\n" for name, text in sorted(groups.items()))
    control = Control(
        admin_group, f"cmsg checkgroups scope:{scope} #{serial}",
        f"checkgroups scope:{scope} #{serial}",
        "application/news-checkgroups; charset=UTF-8", body,
    )  # fmt: skip
    return signed_article(signer, control, **kw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--homedir", type=Path, required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--mail", required=True)
    parser.add_argument("--host", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("make-key")
    sub.add_parser("public-key")
    new = sub.add_parser("newgroup")
    new.add_argument("group")
    new.add_argument("description")
    remove = sub.add_parser("rmgroup")
    remove.add_argument("group")
    args = parser.parse_args()
    signer = Signer(args.homedir, args.user_id, args.name, args.mail, args.host)
    if args.command == "make-key":
        signer.make_key()
    elif args.command == "public-key":
        sys.stdout.write(signer.public_key())
    elif args.command == "newgroup":
        sys.stdout.write(newgroup(signer, args.group, args.description))
    else:
        sys.stdout.write(rmgroup(signer, args.group))


if __name__ == "__main__":
    main()
