#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pydantic",
# ]
# ///

"""
Models for one DSpace item as read through OAI-PMH with ``metadataPrefix=dim``.

``dim`` (DSpace Intermediate Metadata) returns every field of an item as a
``<dim:field mdschema="dc" element="contributor" qualifier="author">`` element.
A record is stored here as JSON: the OAI-PMH header plus a flat list of fields.
"""

import xml.etree.ElementTree as ET
from contextlib import suppress
from datetime import UTC, datetime

from pydantic import BaseModel, Field

# Partial dates DSpace allows in dc.date.issued, besides full ISO 8601 dates.
PARTIAL_DATE_FORMATS: tuple[str, ...] = ("%Y-%m", "%Y")

OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"
DIM_NS = "{http://www.dspace.org/xmlns/dspace/dim}"


def parse_dspace_date(value: str) -> datetime | None:
    """
    Parse a DSpace date such as ``2024``, ``2024-05``, ``2024-05-12`` or
    ``2024-05-12T10:00:00Z``. Dates without a time zone are taken as UTC.
    Returns ``None`` when the value is not a date.
    """
    value = value.strip()
    with suppress(ValueError):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    for date_format in PARTIAL_DATE_FORMATS:
        with suppress(ValueError):
            return datetime.strptime(value, date_format).replace(tzinfo=UTC)
    return None


class DimField(BaseModel):
    """One ``<dim:field>`` element of a DSpace item."""

    mdschema: str = Field(description="Metadata schema, e.g. 'dc'.")
    element: str = Field(description="Element, e.g. 'contributor'.")
    qualifier: str | None = Field(default=None, description="e.g. 'author'.")
    lang: str | None = Field(default=None, description="Language tag, if any.")
    value: str = Field(default="", description="The text of the field.")

    @property
    def key(self) -> str:
        """The qualified Dublin Core name, e.g. ``dc.contributor.author``."""
        return ".".join(
            part for part in (self.mdschema, self.element, self.qualifier) if part
        )


class DSpaceItem(BaseModel):
    """One DSpace item: the OAI-PMH record header and its ``dim`` fields."""

    identifier: str = Field(
        description="OAI identifier, e.g. 'oai:localhost:123456789/5'."
    )
    datestamp: datetime | None = Field(
        default=None, description="When the item last changed, from the header."
    )
    set_specs: list[str] = Field(
        default_factory=list, description="Sets the item is in, e.g. 'col_...'."
    )
    fields: list[DimField] = Field(default_factory=list)

    def get_metadata_values(self, *keys: str) -> list[str]:
        """All values of the given keys, in the order the keys are given."""
        return [
            field.value for key in keys for field in self.fields if field.key == key
        ]

    def get_metadata_value(self, *keys: str, default: str = "") -> str:
        """The first value of the first key that has one, or *default*."""
        values = self.get_metadata_values(*keys)
        return values[0] if values else default

    def get_metadata_values_under(self, key: str) -> list[str]:
        """Values of *key* and of every qualified form, e.g. dc.subject.lcsh."""
        return [
            field.value
            for field in self.fields
            if field.key == key or field.key.startswith(f"{key}.")
        ]

    @property
    def handle(self) -> str:
        """The DSpace handle from the OAI identifier, e.g. '123456789/5'."""
        return self.identifier.rpartition(":")[2]


def item_from_oai_record(record: ET.Element) -> DSpaceItem:
    """Turn an OAI-PMH <record> with metadataPrefix=dim into a DSpaceItem."""
    header = record.find(f"{OAI_NS}header")
    if header is None:
        msg = "The OAI-PMH record has no <header>"
        raise ValueError(msg)
    return DSpaceItem(
        identifier=header.findtext(f"{OAI_NS}identifier", ""),
        datestamp=header.findtext(f"{OAI_NS}datestamp") or None,
        set_specs=[spec.text or "" for spec in header.findall(f"{OAI_NS}setSpec")],
        fields=[
            DimField(
                mdschema=field.get("mdschema", ""),
                element=field.get("element", ""),
                qualifier=field.get("qualifier"),
                lang=field.get("lang"),
                value=field.text or "",
            )
            for field in record.iter(f"{DIM_NS}field")
        ],
    )


if __name__ == "__main__":
    from pathlib import Path

    here = Path(__file__).parent
    item = DSpaceItem.model_validate_json((here / "dspace_item.json").read_text())
    print(f"{item.handle = }")
    print(f"{item.get_metadata_value('dc.title') = }")
    print(f"{item.get_metadata_values('dc.contributor.author') = }")
    print(f"{item.get_metadata_values_under('dc.subject') = }")
