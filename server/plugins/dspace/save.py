"""
Save a card from OME as an item in a DSpace collection (#223).

The deposit goes through the generic SWORD v2 writer in `server/sword.py`. This module
holds what is DSpace specific: the settings, the mapping from `EducationResource` to
Dublin Core terms, the "already exists" check and reading the item UUID from the
deposit receipt.

DSpace maps the terms to its own fields (`swordv2-server.simpledc.*`):
title → dc.title, creator → dc.contributor.author, abstract → dc.description.abstract,
issued → dc.date.issued, type → dc.type, source → dc.source, relation → dc.relation,
subject → dc.subject, publisher → dc.publisher, rights → dc.rights.

The `cms_id` of a saved item is its UUID, never its handle (ADR 0011 §4): in a
collection with a review workflow the item only gets a handle after approval, while
the deposit receipt has the UUID at once, and the UUID never changes.
"""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx2

from server import sword
from server.plugins.ome_plugin import (
    CardAlreadySavedError,
    EducationResource,
    SavedItem,
)

ITEM_TYPE = "Learning Object"
# The edit IRI of a deposit receipt ends in the item UUID:
# http://localhost:8080/server/swordv2/edit/<uuid>
UUID_AT_END = re.compile(
    r"(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)
# How many search hits to compare with the card's source_url. The search matches the
# words of the URL, so the exact match is not always the first hit.
SEARCH_SIZE = 10


class DSpaceItemExistsError(CardAlreadySavedError):
    """The collection already has an item for this card. The save route answers 409."""

    def __init__(self, existing: SavedItem) -> None:
        super().__init__(
            existing, f"DSpace already has this card as {existing.cms_url}"
        )


@dataclass(frozen=True)
class DepositSettings:
    """Where and as whom to deposit. From the `dspace` block in config.json."""

    server_url: str  # e.g. http://dspace:8080/server, where this node reaches DSpace
    collection_handle: str  # e.g. 123456789/2
    username: str  # the OME deposit account, never the admin
    password: str
    # DSpace's own dspace.server.url, e.g. http://localhost:8080/server, when it is not
    # server_url. SWORD only knows the collection under that name (see `deposit_host`).
    public_server_url: str = ""
    # The DSpace website (dspaceUI), e.g. http://localhost:4001, for the item pages.
    ui_url: str = ""

    @classmethod
    def from_env(cls) -> "DepositSettings":
        settings = cls(
            server_url=os.getenv("DSPACE_SERVER_URL", "").rstrip("/"),
            collection_handle=os.getenv("DSPACE_COLLECTION_HANDLE", ""),
            username=os.getenv("DSPACE_DEPOSIT_EMAIL", ""),
            password=os.getenv("DSPACE_DEPOSIT_PASSWORD", ""),
            public_server_url=os.getenv("DSPACE_PUBLIC_SERVER_URL", "").rstrip("/"),
        )
        required = ("server_url", "collection_handle", "username", "password")
        if missing := [name for name in required if not getattr(settings, name)]:
            msg = f"Set these in the dspace block of config.json to save: {missing}"
            raise RuntimeError(msg)
        return settings

    @property
    def collection_url(self) -> str:
        return f"{self.server_url}/swordv2/collection/{self.collection_handle}"

    @property
    def api_url(self) -> str:
        return f"{self.server_url}/api"

    @property
    def deposit_host(self) -> str:
        """
        The Host header for the deposit, or "" to keep the one of `server_url`.

        Inside Docker the backend reaches DSpace at dspace:8080, but SWORD answers 404
        for a collection asked under any other name than its own dspace.server.url.
        """
        if not self.public_server_url:
            return ""
        return urlparse(self.public_server_url).netloc

    def item_url(self, uuid: str) -> str:
        """The page of an item on the DSpace website."""
        return f"{self.ui_url.rstrip('/')}/items/{uuid}"


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


def saved_item_of(
    receipt: sword.DepositReceipt, settings: DepositSettings
) -> SavedItem:
    """The UUID and page of the new item, from the edit IRI of the deposit receipt."""
    if not (match := UUID_AT_END.search(urlparse(receipt.edit_iri).path)):
        msg = f"The deposit receipt has no item UUID in its edit IRI: {receipt}"
        raise sword.SWORDError(201, msg)
    uuid = match["uuid"].lower()
    return SavedItem(cms_id=uuid, cms_url=settings.item_url(uuid))


def _values(item: dict, field: str) -> set[str]:
    """The values of one metadata field of a REST item, without spaces around them."""
    return {value["value"].strip() for value in item.get("metadata", {}).get(field, [])}


def find_existing(
    source_url: str,
    settings: DepositSettings,
    transport: httpx2.BaseTransport | None = None,
) -> SavedItem | None:
    """
    An item that already holds this card, found by its `source_url`.

    Either an item saved from OME before (`dc.relation`), or the library's own item
    that the card was made from (`dc.identifier.uri`). Uses the public search of the
    REST API, so it needs no login. The search matches the words of the URL, so a hit
    only counts when one of those two fields is exactly `source_url`.
    """
    if not (source_url := source_url.strip()):
        return None
    quoted = source_url.replace('"', '\\"')
    query = f'dc.relation:"{quoted}" OR dc.identifier.uri:"{quoted}"'
    params = {"query": query, "dsoType": "ITEM", "size": str(SEARCH_SIZE)}
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
        status = error.response.status_code
        msg = f"DSpace search answered {status}"
        # Only a gateway error means DSpace is down; any other answer is a real refusal.
        if status in sword.UNREACHABLE_STATUSES:
            raise sword.SWORDUnreachableError(msg) from error
        raise sword.SWORDError(status, msg) from error

    result = response.json()["_embedded"]["searchResult"]
    for hit in result.get("_embedded", {}).get("objects", []):
        item = hit["_embedded"]["indexableObject"]
        urls = _values(item, "dc.relation") | _values(item, "dc.identifier.uri")
        if source_url in urls:
            return SavedItem(
                cms_id=item["uuid"], cms_url=settings.item_url(item["uuid"])
            )
    return None


def save_to_dspace(
    resource: EducationResource,
    ome_id: str,
    settings: DepositSettings,
    transport: httpx2.BaseTransport | None = None,
) -> SavedItem:
    """
    Deposit the card as a new item, metadata only, and return its UUID and page.

    Raise `DSpaceItemExistsError` (a `CardAlreadySavedError`) when an item with the
    same `source_url` exists, `SWORDUnreachableError` (a `CMSUnreachableError`) when
    DSpace does not answer and `SWORDLoginError` when DSpace refuses the deposit
    account. DSpace logs a refused login as "Unable to authenticate the onBehalfOf
    account".
    """
    if not settings.ui_url:
        msg = "DepositSettings.ui_url is needed for the page of the saved item"
        raise RuntimeError(msg)
    if existing := find_existing(resource.source_url, settings, transport):
        raise DSpaceItemExistsError(existing)
    collection = sword.SWORDCollection(
        url=settings.collection_url,
        auth=(settings.username, settings.password),
        host=settings.deposit_host,
    )
    receipt = sword.deposit_entry(
        collection, sword.atom_entry(dcterms_of(resource, ome_id)), transport=transport
    )
    return saved_item_of(receipt, settings)
