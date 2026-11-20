import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import pytest

from server import oai_pmh
from server.plugins.dspace.dspace_models import (
    DimField,
    DSpaceItem,
    parse_dspace_date,
)
from server.plugins.dspace.plugin import DSpacePlugin
from server.plugins.ome_plugin import EducationResource

DSPACE_DIR = Path(__file__).parent.parent / "server" / "plugins" / "dspace"

# A shortened GetRecord answer from the local DSpace (metadataPrefix=dim).
GET_RECORD_XML = """\
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record>
<header>
  <identifier>oai:localhost:123456789/3</identifier>
  <datestamp>2026-11-20T10:18:48Z</datestamp>
  <setSpec>col_123456789_2</setSpec>
</header>
<metadata><dim:dim xmlns:dim="http://www.dspace.org/xmlns/dspace/dim">
  <dim:field mdschema="dc" element="contributor" qualifier="author">John Doe</dim:field>
  <dim:field mdschema="dc" element="title">Introduction to AI</dim:field>
  <dim:field mdschema="dc" element="rights" qualifier="uri">https://creativecommons.org/licenses/by/4.0/</dim:field>
</dim:dim></metadata>
</record></GetRecord></OAI-PMH>"""


@pytest.fixture
def item_json() -> str:
    return (DSPACE_DIR / "dspace_item.json").read_text()


def test_item_from_json(item_json: str) -> None:
    """dspace_item.json is a real record from the local DSpace (dev profile)."""
    item = DSpaceItem.model_validate_json(item_json)
    assert item.handle == "123456789/3"
    assert item.set_specs == ["com_123456789_1", "col_123456789_2"]
    assert item.fields[0].key == "dc.contributor.author"
    assert item.fields[-1].key == "others.access-status"


def test_card_from_json(item_json: str) -> None:
    card = DSpacePlugin().make_metadata_card_from_json(item_json)
    assert card.title == "Introduction to Artificial Intelligence and Machine Learning"
    # The abstract wins over the plain dc.description.
    assert card.description.startswith("This textbook introduces")
    assert card.authors == ["Sarah J. Mitchell", "John Doe"]
    assert card.authoring_institution == "Academic Learning Press"
    assert card.subject_tags == [
        "Artificial Intelligence",
        "Machine Learning",
        "Data Science",
        "Neural Networks",
    ]
    assert card.creation_date == datetime(2023, 1, 1, tzinfo=UTC)
    assert card.last_modified_date == datetime(2026, 11, 20, 10, 18, 48, tzinfo=UTC)
    assert card.source_url == "http://localhost:4001/handle/123456789/3"
    # dc.rights.uri wins over the free-text dc.rights.
    assert card.spdx_license_expression == "CC-BY-4.0"


def test_authors_and_subjects_without_doubles() -> None:
    """dc.creator adds to the authors, and qualified subjects are included."""
    item = DSpaceItem(
        identifier="oai:localhost:123456789/8",
        fields=[
            DimField(
                mdschema="dc",
                element="contributor",
                qualifier="author",
                value="Peeters, An",
            ),
            DimField(mdschema="dc", element="creator", value="Peeters, An"),
            DimField(mdschema="dc", element="creator", value="Janssens, Tom"),
            DimField(mdschema="dc", element="subject", value="statistics"),
            DimField(
                mdschema="dc",
                element="subject",
                qualifier="lcsh",
                value="Mathematical statistics",
            ),
            DimField(mdschema="dc", element="subject", value="statistics"),
        ],
    )
    card = DSpacePlugin().make_metadata_card(item)
    assert card.authors == ["Peeters, An", "Janssens, Tom"]
    assert card.subject_tags == ["statistics", "Mathematical statistics"]


def test_card_from_a_minimal_item() -> None:
    """An item with only a title and a free-text licence still makes a card."""
    item = DSpaceItem(
        identifier="oai:localhost:123456789/6",
        fields=[
            DimField(mdschema="dc", element="title", value="Only a title"),
            DimField(mdschema="dc", element="description", value="Plain text"),
            DimField(mdschema="dc", element="rights", value="CC BY 4.0"),
        ],
    )
    card = DSpacePlugin().make_metadata_card(item)
    assert card.title == "Only a title"
    assert card.description == "Plain text"
    assert card.authors == []
    assert card.creation_date is None
    assert card.last_modified_date is None
    assert card.spdx_license_expression == "CC-BY-4.0"


def test_unknown_licence_is_kept() -> None:
    item = DSpaceItem(
        identifier="oai:localhost:123456789/7",
        fields=[DimField(mdschema="dc", element="rights", value="All rights reserved")],
    )
    card = DSpacePlugin().make_metadata_card(item)
    assert card.spdx_license_expression == "All rights reserved"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024", datetime(2024, 1, 1, tzinfo=UTC)),
        ("2024-05", datetime(2024, 5, 1, tzinfo=UTC)),
        ("2024-05-12", datetime(2024, 5, 12, tzinfo=UTC)),
        ("2024-05-12T10:00:00Z", datetime(2024, 5, 12, 10, tzinfo=UTC)),
        ("", None),
        ("spring 2024", None),
    ],
)
def test_parse_dspace_date(value: str, expected: datetime | None) -> None:
    assert parse_dspace_date(value) == expected


def test_summarize(item_json: str) -> None:
    plugin = DSpacePlugin()
    summary = plugin.summarize(plugin.make_metadata_card_from_json(item_json))
    lines = summary.splitlines()
    assert lines[0] == "Introduction to Artificial Intelligence and Machine Learning"
    assert lines[1] == "By Sarah J. Mitchell, John Doe"
    assert lines[3].startswith("This textbook introduces")
    assert lines[-1] == "http://localhost:4001/handle/123456789/3"


def test_summarize_with_only_a_title() -> None:
    assert DSpacePlugin().summarize(EducationResource(title="Only a title")) == (
        "Only a title"
    )


def test_card_from_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handle in the URL becomes the OAI identifier, and the record a card."""
    requests = []

    def fake_get_record(
        base_url: str, identifier: str, metadata_prefix: str
    ) -> ET.Element:
        requests.append((base_url, identifier, metadata_prefix))
        root = ET.fromstring(GET_RECORD_XML)  # noqa: S314 - fixed test XML
        return root.find(".//oai:record", oai_pmh.OAI_NS)

    monkeypatch.setenv("DSPACE_OAI_URL", "http://dspace:8080/server/oai/request")
    monkeypatch.setattr(oai_pmh, "get_record", fake_get_record)

    card = DSpacePlugin().make_metadata_card_from_url(
        "http://localhost:4001/handle/123456789/3"
    )
    assert requests == [
        (
            "http://dspace:8080/server/oai/request",
            "oai:localhost:123456789/3",
            "dim",
        )
    ]
    assert card.title == "Introduction to AI"
    assert card.authors == ["John Doe"]
    assert card.last_modified_date == datetime(2026, 11, 20, 10, 18, 48, tzinfo=UTC)
    assert card.spdx_license_expression == "CC-BY-4.0"


def test_card_from_url_needs_the_dspace_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DSPACE_OAI_URL", raising=False)
    with pytest.raises(RuntimeError, match="DSPACE_OAI_URL"):
        DSpacePlugin().make_metadata_card_from_url(
            "http://localhost:4001/handle/123456789/3"
        )


def test_card_from_url_needs_a_handle_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSPACE_OAI_URL", "http://dspace:8080/server/oai/request")
    with pytest.raises(ValueError, match="handle URL"):
        DSpacePlugin().make_metadata_card_from_url(
            "http://localhost:4001/items/a5f462bd-34f9-4d31-a888-93353c08748d"
        )
