"""
A small, generic SWORD v2 writer (https://sword.cottagelabs.com/previous-versions-of-sword/sword-v2/).

It knows nothing about a specific repository: the collection URL, the login and the
Dublin Core terms are passed in, and the plugins map their card to those terms. Only a
metadata deposit (an Atom entry) is built: OME carries metadata only (ADR 0011 §10).
DSpace uses it now; EPrints, Dataverse and Hyku also take SWORD v2 deposits and could
use it later with their own mapping.
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass

import httpx2

from server.plugins.ome_plugin import CMSUnreachableError

ATOM = "http://www.w3.org/2005/Atom"
DCTERMS = "http://purl.org/dc/terms/"
NS = {"atom": ATOM, "dcterms": DCTERMS}
ENTRY_CONTENT_TYPE = "application/atom+xml;type=entry"
# Answers that mean the repository is down or in the way, not that the deposit is wrong
UNREACHABLE_STATUSES = {502, 503, 504}

ET.register_namespace("atom", ATOM)
ET.register_namespace("dcterms", DCTERMS)


class SWORDError(Exception):
    """The repository refused the deposit."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"SWORD deposit refused ({status}): {message}")
        self.status = status


class SWORDLoginError(SWORDError):
    """The repository refused the login (401 or 403)."""


class SWORDUnreachableError(CMSUnreachableError):
    """The repository did not answer. The save route answers 502."""


@dataclass(frozen=True)
class SWORDCollection:
    """Where and as whom to deposit."""

    url: str  # the collection IRI
    auth: tuple[str, str]  # (username, password) for basic auth
    # A Host header to send instead of the one of `url`: for a repository that is
    # reached on an internal address but only knows its collections under its
    # public name. "" keeps the one of `url`.
    host: str = ""


@dataclass(frozen=True)
class DepositReceipt:
    """What the deposit receipt says about the new item."""

    edit_iri: str
    alternate_url: str
    identifiers: tuple[str, ...]


def atom_entry(terms: Iterable[tuple[str, str]]) -> bytes:
    """
    An Atom entry with one dcterms element per (term, value) pair, in that order.

    Empty values are left out. The first `title` is also the Atom title, which the
    Atom format requires.
    """
    entry = ET.Element(f"{{{ATOM}}}entry")
    title = ET.SubElement(entry, f"{{{ATOM}}}title")
    for term, value in terms:
        if not value:
            continue
        if term == "title" and not title.text:
            title.text = value
        ET.SubElement(entry, f"{{{DCTERMS}}}{term}").text = value
    return ET.tostring(entry, encoding="utf-8", xml_declaration=True)


def _error_message(response: httpx2.Response) -> str:
    """The summary of a SWORD error document, or the start of the body."""
    try:
        root = ET.fromstring(response.content)  # noqa: S314
    except ET.ParseError:
        return response.text[:200] or response.reason_phrase
    summary = root.findtext("atom:summary", "", NS) or root.findtext(
        "atom:title", "", NS
    )
    return summary.strip() or response.reason_phrase


def parse_receipt(content: bytes, location: str = "") -> DepositReceipt:
    """Read the edit IRI, the page of the item and its identifiers from a receipt."""
    root = ET.fromstring(content)  # noqa: S314
    links = {
        link.get("rel"): link.get("href", "") for link in root.iterfind("atom:link", NS)
    }
    identifiers = [root.findtext("atom:id", "", NS)]
    identifiers += [el.text or "" for el in root.iterfind("dcterms:identifier", NS)]
    return DepositReceipt(
        edit_iri=links.get("edit") or location,
        alternate_url=links.get("alternate", ""),
        identifiers=tuple(i.strip() for i in identifiers if i and i.strip()),
    )


def deposit_entry(
    collection: SWORDCollection,
    entry: bytes,
    *,
    in_progress: bool = False,
    transport: httpx2.BaseTransport | None = None,
) -> DepositReceipt:
    """
    POST an Atom entry to a SWORD collection and return the deposit receipt.

    `in_progress=False` asks the repository to complete the deposit at once. Raise
    `SWORDUnreachableError` when the repository does not answer, `SWORDLoginError`
    when it refuses the login and `SWORDError` for any other refusal.
    """
    headers = {
        "Content-Type": ENTRY_CONTENT_TYPE,
        "In-Progress": "true" if in_progress else "false",
    }
    if collection.host:
        headers["Host"] = collection.host
    try:
        with httpx2.Client(
            auth=collection.auth, transport=transport, timeout=30
        ) as client:
            response = client.post(collection.url, content=entry, headers=headers)
    except httpx2.TransportError as error:
        msg = f"SWORD collection {collection.url} did not answer: {error}"
        raise SWORDUnreachableError(msg) from error

    if response.status_code in UNREACHABLE_STATUSES:
        msg = f"SWORD collection {collection.url} answered {response.status_code}"
        raise SWORDUnreachableError(msg)
    if response.status_code in {401, 403}:
        raise SWORDLoginError(response.status_code, _error_message(response))
    if response.status_code != 201:  # 201 Created is the only success
        raise SWORDError(response.status_code, _error_message(response))
    return parse_receipt(response.content, response.headers.get("Location", ""))
