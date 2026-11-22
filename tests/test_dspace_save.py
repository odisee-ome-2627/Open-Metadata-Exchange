"""Saving a card as a DSpace item (#223), against a fake DSpace."""

import base64
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx2
import pytest

from server import sword
from server.plugins.dspace import save
from server.plugins.dspace.plugin import DSpacePlugin
from server.plugins.ome_plugin import (
    CMSUnreachableError,
    EducationResource,
    SavedItem,
    can_save,
)

SETTINGS = save.DepositSettings(
    server_url="http://dspace.test/server",
    collection_handle="123456789/2",
    username="deposit@ome.test",
    password="secret",  # noqa: S106
)
OME_ID = "<abc123@ome.node>"
CARD = EducationResource(
    title="Mobile App Development, les 7",
    description="Offline-first tegenover cache en online-only.",
    authors=["Doe, Jane", "Roe, Rick"],
    authoring_institution="Odisee",
    subject_tags=["offline-first"],
    creation_date=datetime(2026, 9, 1, tzinfo=UTC),
    source_url="https://other.library/handle/1/7",
    spdx_license_expression="CC-BY-4.0",
)
ITEM_URL = "http://localhost:4001/handle/123456789/12"
RECEIPT = f"""<entry xmlns="http://www.w3.org/2005/Atom"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <link rel="edit" href="http://dspace.test/server/swordv2/edit/uuid-12"/>
  <link rel="alternate" href="{ITEM_URL}"/>
</entry>""".encode()


def search_answer(*items: tuple[str, str]) -> dict:
    objects = [
        {
            "_embedded": {
                "indexableObject": {
                    "handle": handle,
                    "metadata": {"dc.identifier.uri": [{"value": url}]},
                }
            }
        }
        for handle, url in items
    ]
    return {"_embedded": {"searchResult": {"_embedded": {"objects": objects}}}}


class FakeDSpace:
    """The public search and the SWORD collection of a DSpace."""

    def __init__(self, *existing: tuple[str, str]) -> None:
        self.existing = existing
        self.searches: list[str] = []
        self.deposits: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/server/api/discover/search/objects":
            self.searches.append(request.url.params["query"])
            return httpx2.Response(200, json=search_answer(*self.existing))
        if request.url.path == "/server/swordv2/collection/123456789/2":
            self.deposits.append(request)
            return httpx2.Response(201, content=RECEIPT)
        return httpx2.Response(404)

    def save(self, card: EducationResource = CARD) -> SavedItem:
        transport = httpx2.MockTransport(self.handle)
        return save.save_to_dspace(card, OME_ID, SETTINGS, transport)


def deposited_terms(request: httpx2.Request) -> list[tuple[str, str]]:
    entry = ET.fromstring(request.content)  # noqa: S314
    return [(el.tag.split("}")[1], el.text or "") for el in entry if "terms" in el.tag]


def test_the_card_becomes_dublin_core_terms() -> None:
    assert save.dcterms_of(CARD, OME_ID) == [
        ("title", CARD.title),
        ("creator", "Doe, Jane"),
        ("creator", "Roe, Rick"),
        ("abstract", CARD.description),
        ("issued", "2026-09-01"),
        ("type", "Learning Object"),
        ("source", OME_ID),
        ("relation", CARD.source_url),
        ("subject", "offline-first"),
        ("publisher", "Odisee"),
        ("rights", "CC-BY-4.0"),
    ]


def test_saving_deposits_the_card_and_returns_handle_and_page() -> None:
    dspace = FakeDSpace()
    assert dspace.save() == SavedItem(cms_id="123456789/12", cms_url=ITEM_URL)
    [deposit] = dspace.deposits
    assert ("source", OME_ID) in deposited_terms(deposit)
    assert deposit.headers["In-Progress"] == "false"


def test_the_deposit_uses_the_deposit_account() -> None:
    dspace = FakeDSpace()
    dspace.save()
    [deposit] = dspace.deposits
    token = base64.b64encode(b"deposit@ome.test:secret").decode()
    assert deposit.headers["Authorization"] == f"Basic {token}"


def test_an_existing_item_with_the_same_source_url_is_not_deposited_again() -> None:
    dspace = FakeDSpace(("123456789/5", "http://localhost:4001/handle/123456789/5"))
    with pytest.raises(save.DSpaceItemExistsError) as error:
        dspace.save()
    assert error.value.existing.cms_id == "123456789/5"
    assert not dspace.deposits
    [query] = dspace.searches
    assert f'dc.relation:"{CARD.source_url}"' in query
    assert f'dc.identifier.uri:"{CARD.source_url}"' in query


def test_a_card_without_source_url_skips_the_search() -> None:
    dspace = FakeDSpace()
    dspace.save(EducationResource(title="No URL"))
    assert not dspace.searches
    assert len(dspace.deposits) == 1


def test_the_handle_can_come_from_the_identifiers() -> None:
    receipt = sword.DepositReceipt(
        edit_iri="", alternate_url="", identifiers=("urn:x", ITEM_URL)
    )
    assert save.saved_item_of(receipt).cms_id == "123456789/12"


def test_a_receipt_without_a_handle_is_an_error() -> None:
    receipt = sword.DepositReceipt(edit_iri="x", alternate_url="", identifiers=())
    with pytest.raises(sword.SWORDError, match="no handle"):
        save.saved_item_of(receipt)


def test_dspace_down_is_unreachable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        msg = "refused"
        raise httpx2.ConnectError(msg, request=request)

    with pytest.raises(CMSUnreachableError):
        save.save_to_dspace(CARD, OME_ID, SETTINGS, httpx2.MockTransport(refuse))


def test_a_refused_login_is_a_login_error() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if "discover" in request.url.path:
            return httpx2.Response(200, json=search_answer())
        return httpx2.Response(403)

    with pytest.raises(sword.SWORDLoginError):
        save.save_to_dspace(CARD, OME_ID, SETTINGS, httpx2.MockTransport(handle))


def test_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "DSPACE_SERVER_URL": "http://dspace:8080/server/",
        "DSPACE_COLLECTION_HANDLE": "123456789/2",
        "DSPACE_DEPOSIT_EMAIL": "deposit@ome.test",
        "DSPACE_DEPOSIT_PASSWORD": "secret",
    }.items():
        monkeypatch.setenv(name, value)
    settings = save.DepositSettings.from_env()
    assert settings.collection_url == (
        "http://dspace:8080/server/swordv2/collection/123456789/2"
    )
    assert settings.api_url == "http://dspace:8080/server/api"


def test_missing_settings_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DSPACE_DEPOSIT_PASSWORD", raising=False)
    monkeypatch.setenv("DSPACE_SERVER_URL", "http://dspace:8080/server")
    with pytest.raises(RuntimeError, match="password"):
        save.DepositSettings.from_env()


def test_the_dspace_plugin_can_save() -> None:
    assert can_save(DSpacePlugin())


def test_save_resource_deposits_with_the_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    def fake_save(resource: EducationResource, ome_id: str) -> SavedItem:
        calls.append((resource, ome_id))
        return SavedItem(cms_id="123456789/12", cms_url=ITEM_URL)

    monkeypatch.setattr("server.plugins.dspace.plugin.save_to_dspace", fake_save)
    saved = DSpacePlugin().save_resource(CARD, OME_ID)
    assert saved.cms_id == "123456789/12"
    assert calls == [(CARD, OME_ID)]
