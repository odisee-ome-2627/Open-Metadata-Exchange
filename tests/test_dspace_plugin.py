import importlib
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from server import oai_pmh
from server.plugins.dspace import plugin as dspace_plugin
from server.plugins.dspace.dspace_models import (
    DimField,
    DSpaceItem,
    parse_dspace_date,
)
from server.plugins.dspace.plugin import DSpacePlugin, dspace_setting
from server.plugins.ome_plugin import (
    EducationResource,
    ItemNotFoundError,
    NotAnItemLinkError,
    OMEPlugin,
)

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


OAI_URL = "http://dspace:8080/server/oai/request"
UI_URL = "http://localhost:4001"
ITEM_UUID = "a5f462bd-34f9-4d31-a888-93353c08748d"


class FakeDSpace:
    """Stands in for OAI-PMH and the REST API, and remembers what was asked."""

    def __init__(self) -> None:
        self.identifiers: list[str] = []
        self.identify_calls = 0
        self.rest_urls: list[str] = []
        self.root_calls = 0
        # uuid -> handle in the REST API; a missing uuid answers 404.
        self.items: dict[str, str | None] = {ITEM_UUID: "123456789/3"}
        self.in_oai = True

    def repository_identifier(self, base_url: str) -> str:
        assert base_url == OAI_URL
        self.identify_calls += 1
        return "dspace.odisee.test"

    def get_record(
        self, base_url: str, identifier: str, metadata_prefix: str
    ) -> ET.Element:
        assert (base_url, metadata_prefix) == (OAI_URL, "dim")
        self.identifiers.append(identifier)
        if not self.in_oai:
            raise oai_pmh.OAIPMHError(oai_pmh.ID_DOES_NOT_EXIST, "no such record")
        root = ET.fromstring(GET_RECORD_XML)  # noqa: S314 - fixed test XML
        return root.find(".//oai:record", oai_pmh.OAI_NS)

    def rest_get(self, url: str, **_kwargs: object) -> httpx2.Response:
        request = httpx2.Request("GET", url)
        if url.endswith("/api"):
            self.root_calls += 1
            return httpx2.Response(200, json={"dspaceUI": UI_URL}, request=request)
        self.rest_urls.append(url)
        uuid = url.rsplit("/", 1)[-1]
        if uuid not in self.items:
            return httpx2.Response(404, json={}, request=request)
        return httpx2.Response(200, json={"handle": self.items[uuid]}, request=request)


@pytest.fixture
def dspace(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeDSpace]:
    """A node with its DSpace at OAI_URL, its website at UI_URL, prefix 123456789."""
    fake = FakeDSpace()
    monkeypatch.setenv("DSPACE_OAI_URL", OAI_URL)
    monkeypatch.setenv("DSPACE_HANDLE_PREFIX", "123456789")
    monkeypatch.delenv("DSPACE_SERVER_URL", raising=False)
    monkeypatch.setattr(oai_pmh, "repository_identifier", fake.repository_identifier)
    monkeypatch.setattr(oai_pmh, "get_record", fake.get_record)
    monkeypatch.setattr(httpx2, "get", fake.rest_get)
    dspace_plugin.repository_identifier.cache_clear()
    dspace_plugin.ui_url.cache_clear()
    yield fake
    dspace_plugin.repository_identifier.cache_clear()
    dspace_plugin.ui_url.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        f"{UI_URL}/handle/123456789/3",
        f"{UI_URL}/handle/123456789/3?show=full",
        f"{UI_URL}/handle/123456789/3/",
        "https://hdl.handle.net/123456789/3",
        "http://hdl.handle.net/123456789/3",
        f"{UI_URL}/items/{ITEM_UUID}",
        f"{UI_URL}/items/{ITEM_UUID.upper()}",
    ],
)
def test_card_from_each_kind_of_link(dspace: FakeDSpace, url: str) -> None:
    """Every link form ends up at the same OAI record, named by the repository."""
    card = DSpacePlugin().make_metadata_card_from_url(url)
    assert dspace.identifiers == ["oai:dspace.odisee.test:123456789/3"]
    assert card.title == "Introduction to AI"
    assert card.authors == ["John Doe"]
    assert card.last_modified_date == datetime(2026, 11, 20, 10, 18, 48, tzinfo=UTC)
    assert card.spdx_license_expression == "CC-BY-4.0"


def test_items_link_asks_the_rest_api_for_the_handle(dspace: FakeDSpace) -> None:
    DSpacePlugin().make_metadata_card_from_url(f"{UI_URL}/items/{ITEM_UUID}")
    assert dspace.rest_urls == [f"http://dspace:8080/server/api/core/items/{ITEM_UUID}"]


def test_server_url_setting_wins_over_the_oai_url(
    dspace: FakeDSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DSPACE_SERVER_URL", "https://api.dspace.test/server/")
    DSpacePlugin().make_metadata_card_from_url(f"{UI_URL}/items/{ITEM_UUID}")
    assert dspace.rest_urls == [
        f"https://api.dspace.test/server/api/core/items/{ITEM_UUID}"
    ]


def test_identify_and_the_website_are_asked_only_once(dspace: FakeDSpace) -> None:
    plugin = DSpacePlugin()
    plugin.make_metadata_card_from_url(f"{UI_URL}/handle/123456789/3")
    plugin.make_metadata_card_from_url(f"{UI_URL}/handle/123456789/3")
    plugin.make_metadata_card_from_url("https://hdl.handle.net/123456789/3")
    assert dspace.identify_calls == 1
    assert dspace.root_calls == 1


def test_the_website_comes_from_the_rest_api(dspace: FakeDSpace) -> None:
    """dspaceUI of <server>/api, not a setting, says which website is ours."""
    assert dspace_plugin.ui_url("http://dspace:8080/server") == UI_URL
    assert dspace.root_calls == 1


@pytest.mark.parametrize(
    "url",
    [
        # Another repository with the same default prefix: not our item /3.
        "https://dspace.other-library.test/handle/123456789/3",
        "https://dspace.other-library.test/items/" + ITEM_UUID,
        # Our host, but another port.
        "http://localhost:8080/handle/123456789/3",
        # The handle resolver, but with another library's prefix.
        "https://hdl.handle.net/20.500.12345/3",
        # Our UI, but a handle with another prefix.
        f"{UI_URL}/handle/20.500.12345/3",
        # Our UI, but not an item page.
        f"{UI_URL}/collections/{ITEM_UUID}",
        f"{UI_URL}/handle/123456789",
        "not a link",
    ],
)
def test_links_that_are_not_our_items_are_refused(dspace: FakeDSpace, url: str) -> None:
    with pytest.raises(NotAnItemLinkError, match="not an item of the"):
        DSpacePlugin().make_metadata_card_from_url(url)
    assert dspace.identifiers == []
    assert dspace.rest_urls == []


@pytest.mark.usefixtures("dspace")
def test_handle_resolver_needs_the_handle_prefix_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without our prefix, a hdl.handle.net link cannot be checked, so it is refused."""
    monkeypatch.delenv("DSPACE_HANDLE_PREFIX")
    with pytest.raises(NotAnItemLinkError):
        DSpacePlugin().make_metadata_card_from_url("https://hdl.handle.net/123456789/3")


def test_item_not_in_oai_pmh_yet(dspace: FakeDSpace) -> None:
    dspace.in_oai = False
    with pytest.raises(ItemNotFoundError, match="dspace oai import"):
        DSpacePlugin().make_metadata_card_from_url(f"{UI_URL}/handle/123456789/99")


def test_unknown_uuid(dspace: FakeDSpace) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ItemNotFoundError, match=unknown):
        DSpacePlugin().make_metadata_card_from_url(f"{UI_URL}/items/{unknown}")
    assert dspace.identifiers == []


def test_uuid_without_a_handle(dspace: FakeDSpace) -> None:
    dspace.items[ITEM_UUID] = None
    with pytest.raises(ItemNotFoundError, match="no handle"):
        DSpacePlugin().make_metadata_card_from_url(f"{UI_URL}/items/{ITEM_UUID}")


def test_card_from_url_needs_the_dspace_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DSPACE_OAI_URL", raising=False)
    with pytest.raises(RuntimeError, match="DSPACE_OAI_URL"):
        DSpacePlugin().make_metadata_card_from_url(f"{UI_URL}/handle/123456789/3")


def test_dspace_setting_reads_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSPACE_SITE_NAME", "Odisee")
    assert dspace_setting("SITE_NAME", "default") == "Odisee"


@pytest.mark.parametrize("value", [None, ""])
def test_dspace_setting_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """No variable, or an empty one from env.j2, gives the default."""
    if value is None:
        monkeypatch.delenv("DSPACE_SITE_NAME", raising=False)
    else:
        monkeypatch.setenv("DSPACE_SITE_NAME", value)
    assert dspace_setting("SITE_NAME", "default") == "default"
    assert dspace_setting("SITE_NAME") == ""


@pytest.fixture
def reload_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[], type[DSpacePlugin]]]:
    """Load plugin.py again after the test sets its variables, and restore it after."""
    yield lambda: importlib.reload(dspace_plugin).DSpacePlugin
    for name in ("NEWSGROUP", "SITE_NAME", "LOGO", "LIBRARIAN_CONTACT"):
        monkeypatch.delenv(f"DSPACE_{name}", raising=False)
    importlib.reload(dspace_plugin)


def test_class_values_come_from_the_dspace_settings(
    monkeypatch: pytest.MonkeyPatch,
    reload_plugin: Callable[[], type[DSpacePlugin]],
) -> None:
    monkeypatch.setenv("DSPACE_NEWSGROUP", "ome.odisee")
    monkeypatch.setenv("DSPACE_SITE_NAME", "Odisee")
    monkeypatch.setenv("DSPACE_LOGO", "https://example.org/logo.png")
    monkeypatch.setenv("DSPACE_LIBRARIAN_CONTACT", "librarian@example.org")
    plugin_class = reload_plugin()
    assert list(plugin_class.newsgroups) == ["ome.odisee"]
    assert plugin_class.site_name == "Odisee"
    assert plugin_class.logo == "https://example.org/logo.png"
    assert plugin_class.librarian_contact == "librarian@example.org"


def test_without_dspace_settings_the_defaults_apply(
    monkeypatch: pytest.MonkeyPatch,
    reload_plugin: Callable[[], type[DSpacePlugin]],
) -> None:
    """A node without a dspace block has no DSpace channel and still starts."""
    for name in ("NEWSGROUP", "SITE_NAME", "LOGO", "LIBRARIAN_CONTACT"):
        monkeypatch.setenv(f"DSPACE_{name}", "")
    plugin_class = reload_plugin()
    assert dict(plugin_class.newsgroups) == {}
    assert plugin_class.site_name == OMEPlugin.site_name
    assert plugin_class.logo == OMEPlugin.logo
    assert plugin_class.librarian_contact == OMEPlugin.librarian_contact
