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
from types import MappingProxyType
from urllib.parse import urlparse

from server import oai_pmh
from server.plugins.dspace.dspace_models import (
    DSpaceItem,
    item_from_oai_record,
    parse_dspace_date,
)
from server.plugins.dspace.save import HANDLE_IN_URL, save_to_dspace
from server.plugins.oapen_books.oapen_models import OAPEN_LICENSE_TO_SPDX
from server.plugins.ome_plugin import EducationResource, OMEPlugin, SavedItem


def to_spdx(licence: str) -> str:
    """Translate a licence URL or name to SPDX, or keep it as it is."""
    return OAPEN_LICENSE_TO_SPDX.get(licence.strip().lower(), licence.strip())


def dspace_setting(name: str, default: str = "") -> str:
    """A value from the dspace block in config.json, through env.j2."""
    return os.getenv(f"DSPACE_{name}") or default


NEWSGROUP = dspace_setting("NEWSGROUP")


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
        """Read one DSpace item through OAI-PMH and turn it into a card."""
        # Read the setting here, not at import time: a node without DSpace must start.
        oai_url = os.getenv("DSPACE_OAI_URL")
        if not oai_url:
            msg = "DSPACE_OAI_URL is not set: this node has no DSpace configured"
            raise RuntimeError(msg)
        if not (match := HANDLE_IN_URL.search(url)):
            msg = f"Not a DSpace handle URL (expected .../handle/<prefix>/<id>): {url}"
            raise ValueError(msg)
        # DSpace builds OAI identifiers as oai:<hostname>:<handle>.
        identifier = f"oai:{urlparse(url).hostname}:{match['handle']}"
        record = oai_pmh.get_record(oai_url, identifier, metadata_prefix="dim")
        return self.make_metadata_card(item_from_oai_record(record))

    def make_metadata_card_from_json(self, json_payload: str) -> EducationResource:
        """Create an EducationResource from a raw JSON string."""
        return self.make_metadata_card(DSpaceItem.model_validate_json(json_payload))

    def save_resource(self, resource: EducationResource, ome_id: str) -> SavedItem:
        """Deposit a card from the network as a new DSpace item, through SWORD v2.

        Metadata only: the file stays behind the card's `source_url` (ADR 0011 §10).
        See `save_to_dspace()` in `save.py` for the settings and the errors.
        """
        return save_to_dspace(resource, ome_id)


if __name__ == "__main__":
    from pathlib import Path

    plugin = DSpacePlugin()
    print(f"{plugin = }")
    print(f"{plugin.mimetypes = }")
    print(f"{plugin.newsgroups = }\n")

    json_path = Path(__file__).parent / "dspace_item.json"
    print(f"{plugin.make_metadata_card_from_json(json_path.read_text()) = }")
