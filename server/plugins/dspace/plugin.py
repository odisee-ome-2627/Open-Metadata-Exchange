#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx2",
#     "pydantic",
# ]
# ///

import os
import re
from dataclasses import replace
from functools import cache
from types import MappingProxyType
from urllib.parse import urlparse

import httpx2

from server import oai_pmh
from server.plugins.dspace.dspace_models import (
    DSpaceItem,
    item_from_oai_record,
    parse_dspace_date,
)
from server.plugins.dspace.save import DepositSettings, save_to_dspace
from server.plugins.oapen_books.oapen_models import OAPEN_LICENSE_TO_SPDX
from server.plugins.ome_plugin import (
    EducationResource,
    ItemNotFoundError,
    NotAnItemLinkError,
    OMEPlugin,
    SavedItem,
)

# The three kinds of item links a librarian can paste:
# a handle URL on the DSpace UI, e.g. http://localhost:4001/handle/123456789/3
HANDLE_IN_URL = re.compile(r"^/handle/(?P<handle>[^/]+/[^/]+)/?$")
# the global handle resolver, e.g. https://hdl.handle.net/123456789/3
HANDLE_RESOLVER_HOST = "hdl.handle.net"
HANDLE_ON_RESOLVER = re.compile(r"^/(?P<handle>[^/]+/[^/]+)/?$")
# the item page of the DSpace 7+ UI, e.g. http://localhost:4001/items/<uuid>
ITEM_UUID_IN_URL = re.compile(
    r"^/items/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)
DEFAULT_PORTS = MappingProxyType({"http": 80, "https": 443})


def to_spdx(licence: str) -> str:
    """Translate a licence URL or name to SPDX, or keep it as it is."""
    return OAPEN_LICENSE_TO_SPDX.get(licence.strip().lower(), licence.strip())


def dspace_setting(name: str, default: str = "") -> str:
    """A value from the dspace block in config.json, through env.j2."""
    return os.getenv(f"DSPACE_{name}") or default


NEWSGROUP = dspace_setting("NEWSGROUP")


def oai_url() -> str:
    """
    The OAI-PMH endpoint: DSPACE_OAI_URL, or else ``/oai/request`` under
    DSPACE_SERVER_URL (e.g. http://dspace:8080/server/oai/request), or "".

    Both point at the same DSpace, so a library only needs to fill in server_url.
    """
    if url := dspace_setting("OAI_URL"):
        return url
    if url := dspace_setting("SERVER_URL"):
        return f"{url.rstrip('/')}/oai/request"
    return ""


def _host_and_port(url: str) -> tuple[str, int | None]:
    """The host and port of a URL, with the default port filled in."""
    parts = urlparse(url)
    return (parts.hostname or "").lower(), parts.port or DEFAULT_PORTS.get(
        parts.scheme.lower()
    )


def server_url(oai_url: str) -> str:
    """
    The base URL of the DSpace backend (REST API and OAI-PMH).

    DSPACE_SERVER_URL wins when it is set; otherwise it is the OAI-PMH URL
    without ``/oai/request``, e.g. http://dspace:8080/server.
    """
    if url := dspace_setting("SERVER_URL"):
        return url.rstrip("/")
    return oai_url.rstrip("/").removesuffix("/oai/request")


@cache
def ui_url(dspace_server_url: str) -> str:
    """
    The address of the DSpace website, e.g. http://localhost:4001.

    The REST API root names it in ``dspaceUI`` (no login needed). It is asked once
    and kept, like the repositoryIdentifier.
    """
    response = httpx2.get(f"{dspace_server_url}/api", timeout=30)
    if not (url := response.raise_for_status().json().get("dspaceUI")):
        msg = f"{dspace_server_url}/api does not name the DSpace website (dspaceUI)"
        raise RuntimeError(msg)
    return url


@cache
def repository_identifier(oai_url: str) -> str:
    """Ask the repository its OAI identifier once, and keep it."""
    return oai_pmh.repository_identifier(oai_url)


class DSpacePlugin(OMEPlugin):
    """
    Plugin for a DSpace repository (https://dspace.lyrasis.org).

    DSpace items are read through OAI-PMH with ``metadataPrefix=dim``, which
    returns every field as qualified Dublin Core, and become OME metadata cards.
    """

    mimetypes: tuple[str, ...] = ("application/vnd.dspace.item+json",)
    # newsgroups is a dict but make it immutable for safety reasons. `ruff rule RUF012`
    newsgroups: dict[str, str] = MappingProxyType(
        {NEWSGROUP: "Metadata from a DSpace repository"} if NEWSGROUP else {}
    )
    site_name: str = dspace_setting("SITE_NAME", OMEPlugin.site_name)
    librarian_contact: str = dspace_setting(
        "LIBRARIAN_CONTACT", OMEPlugin.librarian_contact
    )
    logo: str = dspace_setting("LOGO", OMEPlugin.logo)

    def make_metadata_card(self, item: DSpaceItem) -> EducationResource:
        """Translate the dim fields of a DSpace item into an EducationResource."""
        issued = parse_dspace_date(item.get_metadata_value("dc.date.issued"))
        # Keep the order and drop doubles when an author is in both fields.
        authors = item.get_metadata_values("dc.contributor.author", "dc.creator")
        licence = item.get_metadata_value("dc.rights.uri", "dc.rights")

        return EducationResource(
            title=item.get_metadata_value("dc.title"),
            description=item.get_metadata_value(
                "dc.description.abstract", "dc.description"
            ),
            authors=list(dict.fromkeys(authors)),
            authoring_institution=item.get_metadata_value("dc.publisher"),
            subject_tags=list(
                dict.fromkeys(item.get_metadata_values_under("dc.subject"))
            ),
            creation_date=issued,
            # The OAI-PMH datestamp is when the item last changed in DSpace.
            last_modified_date=item.datestamp or issued,
            source_url=item.get_metadata_value("dc.identifier.uri"),
            spdx_license_expression=to_spdx(licence) if licence else "",
        )

    def summarize(self, card: EducationResource) -> str:
        """Short plain-text summary for the body of the NNTP article."""
        lines = [card.title]
        if card.authors:
            lines.append("By " + ", ".join(card.authors))
        if card.description:
            lines += ["", card.description]
        if card.source_url:
            lines += ["", card.source_url]
        return "\n".join(lines)

    def make_metadata_card_from_url(self, url: str) -> EducationResource:
        """
        Read one DSpace item through OAI-PMH and turn it into a card.

        The link may be ``<website>/handle/<prefix>/<id>``,
        ``https://hdl.handle.net/<prefix>/<id>`` or ``<website>/items/<uuid>``,
        where <website> is the address DSpace gives as ``dspaceUI``.
        A link that is not an item of this node's DSpace raises NotAnItemLinkError,
        an item DSpace or OAI-PMH does not know raises ItemNotFoundError.
        """
        # Read the setting here, not at import time: a node without DSpace must start.
        if not (endpoint := oai_url()):
            msg = (
                "Neither DSPACE_SERVER_URL nor DSPACE_OAI_URL is set: "
                "this node has no DSpace configured"
            )
            raise RuntimeError(msg)
        handle = self.handle_from_url(url, endpoint)
        # In OAI-PMH the repository decides the identifier, not the pasted link.
        identifier = f"oai:{repository_identifier(endpoint)}:{handle}"
        try:
            record = oai_pmh.get_record(endpoint, identifier, metadata_prefix="dim")
        except oai_pmh.OAIPMHError as error:
            if error.code == oai_pmh.ID_DOES_NOT_EXIST:
                msg = (
                    "This item is not in OAI-PMH yet. Run `dspace oai import`, "
                    "or check the link."
                )
                raise ItemNotFoundError(msg) from error
            raise
        return self.make_metadata_card(item_from_oai_record(record))

    def not_our_item(self, url: str) -> NotAnItemLinkError:
        return NotAnItemLinkError(
            f"This link is not an item of the {self.site_name} DSpace: {url}"
        )

    def handle_from_url(self, url: str, oai_url: str) -> str:
        """The handle of the item a link points to, if it is an item of our DSpace."""
        parts = urlparse(url.strip())
        handle_prefix = dspace_setting("HANDLE_PREFIX").strip("/")
        dspace_server_url = server_url(oai_url)

        if (parts.hostname or "").lower() == HANDLE_RESOLVER_HOST:
            match = HANDLE_ON_RESOLVER.match(parts.path)
            # Every test DSpace uses prefix 123456789, so only our own prefix is ours.
            if not (match and handle_prefix):
                raise self.not_our_item(url)
            handle = match["handle"]
        elif _host_and_port(url) == _host_and_port(ui_url(dspace_server_url)):
            if match := HANDLE_IN_URL.match(parts.path):
                handle = match["handle"]
            elif match := ITEM_UUID_IN_URL.match(parts.path):
                handle = self.handle_of_item(dspace_server_url, match["uuid"].lower())
            else:
                raise self.not_our_item(url)
        else:
            raise self.not_our_item(url)

        if handle_prefix and not handle.startswith(f"{handle_prefix}/"):
            raise self.not_our_item(url)
        return handle

    def handle_of_item(self, dspace_server_url: str, uuid: str) -> str:
        """Ask the DSpace REST API for the handle of an item (no login needed)."""
        response = httpx2.get(f"{dspace_server_url}/api/core/items/{uuid}", timeout=30)
        if response.status_code == httpx2.codes.NOT_FOUND:
            msg = f"The {self.site_name} DSpace has no item {uuid}. Check the link."
            raise ItemNotFoundError(msg)
        if not (handle := response.raise_for_status().json().get("handle")):
            msg = f"Item {uuid} has no handle yet, so it is not in OAI-PMH."
            raise ItemNotFoundError(msg)
        return handle

    def make_metadata_card_from_json(self, json_payload: str) -> EducationResource:
        """Create an EducationResource from a raw JSON string."""
        return self.make_metadata_card(DSpaceItem.model_validate_json(json_payload))

    def save_resource(self, resource: EducationResource, ome_id: str) -> SavedItem:
        """Deposit a card from the network as a new DSpace item, through SWORD v2.

        Metadata only: the file stays behind the card's `source_url` (ADR 0011 §10).
        The item page is `<website>/items/<uuid>`, with the website that DSpace names
        as ``dspaceUI``, like the publish links. See `save_to_dspace()` in `save.py`
        for the settings and the errors.
        """
        settings = DepositSettings.from_env()
        settings = replace(settings, ui_url=ui_url(settings.server_url))
        return save_to_dspace(resource, ome_id, settings)


if __name__ == "__main__":
    from pathlib import Path

    plugin = DSpacePlugin()
    print(f"{plugin = }")
    print(f"{plugin.mimetypes = }")
    print(f"{plugin.newsgroups = }\n")

    json_path = Path(__file__).parent / "dspace_item.json"
    print(f"{plugin.make_metadata_card_from_json(json_path.read_text()) = }")
