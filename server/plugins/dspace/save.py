"""
Save a card from OME as an item in a DSpace collection (#223).

The deposit goes through the generic SWORD v2 writer in `server/sword.py`. This module
holds what is DSpace specific: the settings, the mapping from `EducationResource` to
Dublin Core terms, the "already exists" check and reading the handle from the receipt.

DSpace maps the terms to its own fields (`swordv2-server.simpledc.*`):
title → dc.title, creator → dc.contributor.author, abstract → dc.description.abstract,
issued → dc.date.issued, type → dc.type, source → dc.source, relation → dc.relation,
subject → dc.subject, publisher → dc.publisher, rights → dc.rights.
"""

import os
import re
from dataclasses import dataclass

import httpx2

from server import sword
from server.plugins.ome_plugin import EducationResource, SavedItem

# A DSpace handle URL, e.g. http://localhost:4001/handle/123456789/3
HANDLE_IN_URL = re.compile(r"/handle/(?P<handle>[^/?#]+/[^/?#]+)")
ITEM_TYPE = "Learning Object"


class DSpaceItemExistsError(Exception):
    """The collection already has an item for this card. The save route answers 409."""

    def __init__(self, existing: SavedItem) -> None:
        super().__init__(f"DSpace already has this card as {existing.cms_url}")
        self.existing = existing


@dataclass(frozen=True)
class DepositSettings:
    """Where and as whom to deposit. From the `dspace` block in config.json."""

    server_url: str  # e.g. http://dspace:8080/server
    collection_handle: str  # e.g. 123456789/2
    username: str  # the OME deposit account, never the admin
    password: str

    @classmethod
    def from_env(cls) -> "DepositSettings":
        settings = cls(
            server_url=os.getenv("DSPACE_SERVER_URL", "").rstrip("/"),
            collection_handle=os.getenv("DSPACE_COLLECTION_HANDLE", ""),
            username=os.getenv("DSPACE_DEPOSIT_EMAIL", ""),
            password=os.getenv("DSPACE_DEPOSIT_PASSWORD", ""),
        )
        if missing := [name for name, value in vars(settings).items() if not value]:
            msg = f"Set these in the dspace block of config.json to save: {missing}"
            raise RuntimeError(msg)
        return settings

    @property
    def collection_url(self) -> str:
        return f"{self.server_url}/swordv2/collection/{self.collection_handle}"

    @property
    def api_url(self) -> str:
        return f"{self.server_url}/api"


def dcterms_of(resource: EducationResource, ome_id: str) -> list[tuple[str, str]]:
    """
    The card as Dublin Core terms for a SWORD deposit.

    `source` is the OME Message-ID: it marks the item as saved from OME, so the export
    skips it. `relation` is the card's `source_url`, where the file stays,
    and the "already exists" check looks for it.
    """
    issued = resource.creation_date or resource.last_modified_date
    return [
        ("title", resource.title),
        *(("creator", author) for author in resource.authors),
        ("abstract", resource.description),
        ("issued", issued.date().isoformat() if issued else ""),
        ("type", ITEM_TYPE),
        ("source", ome_id),
        ("relation", resource.source_url),
        *(("subject", subject) for subject in resource.subject_tags),
        ("publisher", resource.authoring_institution),
        ("rights", resource.spdx_license_expression),
    ]


def handle_in(url: str) -> str:
    """The handle in a DSpace handle URL, or "" when there is none."""
    match = HANDLE_IN_URL.search(url)
    return match["handle"] if match else ""


def saved_item_of(receipt: sword.DepositReceipt) -> SavedItem:
    """The handle and page of the new item, both from the one deposit receipt."""
    for url in (receipt.alternate_url, *receipt.identifiers):
        if handle := handle_in(url):
            return SavedItem(cms_id=handle, cms_url=url)
    msg = f"The deposit receipt has no handle: {receipt}"
    raise sword.SWORDError(201, msg)


def find_existing(
    source_url: str,
    settings: DepositSettings,
    transport: httpx2.BaseTransport | None = None,
) -> SavedItem | None:
    """
    An item that already holds this card, found by its `source_url`.

    Either an item saved from OME before (`dc.relation`), or the library's own item
    that the card was made from (`dc.identifier.uri`). Uses the public search of the
    REST API, so it needs no login.
    """
    if not source_url:
        return None
    quoted = source_url.replace('"', '\\"')
    query = f'dc.relation:"{quoted}" OR dc.identifier.uri:"{quoted}"'
    params = {"query": query, "dsoType": "ITEM", "size": "1"}
    try:
        with httpx2.Client(transport=transport, timeout=30) as client:
            response = client.get(
                f"{settings.api_url}/discover/search/objects", params=params
            )
            response.raise_for_status()
    except httpx2.TransportError as error:
        msg = f"DSpace at {settings.api_url} did not answer: {error}"
        raise sword.SWORDUnreachableError(msg) from error
    except httpx2.HTTPStatusError as error:
        msg = f"DSpace search answered {error.response.status_code}"
        raise sword.SWORDUnreachableError(msg) from error

    result = response.json()["_embedded"]["searchResult"]
    for hit in result.get("_embedded", {}).get("objects", []):
        item = hit["_embedded"]["indexableObject"]
        uris = item.get("metadata", {}).get("dc.identifier.uri", [])
        url = uris[0]["value"] if uris else ""
        return SavedItem(cms_id=item["handle"], cms_url=url)
    return None


def save_to_dspace(
    resource: EducationResource,
    ome_id: str,
    settings: DepositSettings | None = None,
    transport: httpx2.BaseTransport | None = None,
) -> SavedItem:
    """
    Deposit the card as a new item, metadata only, and return its handle and page.

    Raise `DSpaceItemExistsError` when an item with the same `source_url` exists,
    `SWORDUnreachableError` (a `CMSUnreachableError`) when DSpace does not answer and
    `SWORDLoginError` when DSpace refuses the deposit account. DSpace logs a refused
    login as "Unable to authenticate the onBehalfOf account".
    """
    settings = settings or DepositSettings.from_env()
    if existing := find_existing(resource.source_url, settings, transport):
        raise DSpaceItemExistsError(existing)
    receipt = sword.deposit_entry(
        settings.collection_url,
        sword.atom_entry(dcterms_of(resource, ome_id)),
        auth=(settings.username, settings.password),
        transport=transport,
    )
    return saved_item_of(receipt)
