"""Saving a card as a DSpace item (#223), against a fake DSpace."""

import base64
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime

import httpx2
import pytest

from server import sword
from server.plugins.dspace import plugin as dspace_plugin
from server.plugins.dspace import save
from server.plugins.dspace.plugin import DSpacePlugin
from server.plugins.ome_plugin import (
    CardAlreadySavedError,
    CMSUnreachableError,
    EducationResource,
    SavedItem,
    can_save,
)

UI_URL = "http://localhost:4001"
SETTINGS = save.DepositSettings(
    server_url="http://dspace.test/server",
    collection_handle="123456789/2",
    username="deposit@ome.test",
    password="secret",  # noqa: S106
    ui_url=UI_URL,
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
NEW_UUID = "1f0b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
OLD_UUID = "a5f462bd-34f9-4d31-a888-93353c08748d"
# DSpace builds the edit IRI with its own dspace.server.url, not the address we used.
RECEIPT = f"""<entry xmlns="http://www.w3.org/2005/Atom"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <link rel="edit" href="http://localhost:8080/server/swordv2/edit/{NEW_UUID}"/>
</entry>""".encode()


def hit(uuid: str, *, relation: str = "", uri: str = "") -> dict:
    """One item of the REST search, with its dc.relation and dc.identifier.uri."""
    metadata = {
        field: [{"value": value}]
        for field, value in (("dc.relation", relation), ("dc.identifier.uri", uri))
        if value
    }
    return {"_embedded": {"indexableObject": {"uuid": uuid, "metadata": metadata}}}


def search_answer(*hits: dict) -> dict:
    return {"_embedded": {"searchResult": {"_embedded": {"objects": list(hits)}}}}


class FakeDSpace:
    """The public search and the SWORD collection of a DSpace."""

    def __init__(self, *hits: dict, search_status: int = 200) -> None:
        self.hits = hits
        self.search_status = search_status
        self.searches: list[httpx2.Request] = []
        self.deposits: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/server/api/discover/search/objects":
            self.searches.append(request)
            if self.search_status != httpx2.codes.OK:
                return httpx2.Response(self.search_status)
            return httpx2.Response(200, json=search_answer(*self.hits))
        if request.url.path == "/server/swordv2/collection/123456789/2":
            self.deposits.append(request)
            return httpx2.Response(201, content=RECEIPT)
        return httpx2.Response(404)

    def save(
        self,
        card: EducationResource = CARD,
        settings: save.DepositSettings = SETTINGS,
    ) -> SavedItem:
        transport = httpx2.MockTransport(self.handle)
        return save.save_to_dspace(card, OME_ID, settings, transport)


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


def test_saving_returns_the_uuid_and_the_item_page() -> None:
    """ADR 0011 §4: the cms_id is the UUID, also before the item has a handle."""
    dspace = FakeDSpace()
    assert dspace.save() == SavedItem(
        cms_id=NEW_UUID, cms_url=f"{UI_URL}/items/{NEW_UUID}"
    )
    [deposit] = dspace.deposits
    assert ("source", OME_ID) in deposited_terms(deposit)
    assert deposit.headers["In-Progress"] == "false"


def test_the_deposit_uses_the_deposit_account() -> None:
    dspace = FakeDSpace()
    dspace.save()
    [deposit] = dspace.deposits
    token = base64.b64encode(b"deposit@ome.test:secret").decode()
    assert deposit.headers["Authorization"] == f"Basic {token}"


def test_without_a_public_server_url_the_host_is_the_one_of_server_url() -> None:
    dspace = FakeDSpace()
    dspace.save()
    [deposit] = dspace.deposits
    assert deposit.headers["Host"] == "dspace.test"


def test_the_deposit_carries_the_host_of_the_public_server_url() -> None:
    """SWORD only knows the collection under dspace.server.url (Docker: dspace:8080)."""
    dspace = FakeDSpace()
    dspace.save(
        settings=replace(SETTINGS, public_server_url="http://localhost:8080/server")
    )
    [deposit] = dspace.deposits
    assert deposit.url.host == "dspace.test"  # still sent to server_url
    assert deposit.headers["Host"] == "localhost:8080"


@pytest.mark.parametrize("field", ["relation", "uri"])
def test_an_item_with_exactly_this_source_url_is_not_deposited_again(
    field: str,
) -> None:
    dspace = FakeDSpace(hit(OLD_UUID, **{field: CARD.source_url}))
    with pytest.raises(save.DSpaceItemExistsError) as error:
        dspace.save()
    assert error.value.existing == SavedItem(
        cms_id=OLD_UUID, cms_url=f"{UI_URL}/items/{OLD_UUID}"
    )
    assert not dspace.deposits
    [search] = dspace.searches
    query = search.url.params["query"]
    assert f'dc.relation:"{CARD.source_url}"' in query
    assert f'dc.identifier.uri:"{CARD.source_url}"' in query


def test_a_hit_with_a_longer_url_is_not_a_duplicate() -> None:
    """The search matches the words of the URL, so it also finds longer URLs."""
    card = CARD.model_copy(update={"source_url": "https://openstax.org/details/books"})
    longer = "https://openstax.org/details/books/introduction-computer-science"
    dspace = FakeDSpace(hit(OLD_UUID, relation=longer), hit(NEW_UUID, uri=longer))
    dspace.save(card)
    assert len(dspace.deposits) == 1


def test_the_exact_match_need_not_be_the_first_hit() -> None:
    longer = f"{CARD.source_url}/more"
    dspace = FakeDSpace(hit(NEW_UUID, uri=longer), hit(OLD_UUID, uri=CARD.source_url))
    with pytest.raises(save.DSpaceItemExistsError) as error:
        dspace.save()
    assert error.value.existing.cms_id == OLD_UUID
    [search] = dspace.searches
    assert search.url.params["size"] == str(save.SEARCH_SIZE)


def test_the_exists_error_is_the_generic_card_already_saved_error() -> None:
    """main.py can answer 409 without knowing DSpace (#325)."""
    existing = SavedItem(cms_id=OLD_UUID, cms_url=f"{UI_URL}/items/{OLD_UUID}")
    error = save.DSpaceItemExistsError(existing)
    assert isinstance(error, CardAlreadySavedError)
    assert error.existing == existing


def test_a_card_without_source_url_skips_the_search() -> None:
    dspace = FakeDSpace()
    dspace.save(EducationResource(title="No URL"))
    assert not dspace.searches
    assert len(dspace.deposits) == 1


def test_a_receipt_without_a_uuid_in_the_edit_iri_is_an_error() -> None:
    receipt = sword.DepositReceipt(
        edit_iri="http://localhost:8080/server/swordv2/edit/",
        alternate_url="",
        identifiers=(),
    )
    with pytest.raises(sword.SWORDError, match="no item UUID"):
        save.saved_item_of(receipt, SETTINGS)


def test_a_receipt_without_a_handle_is_fine() -> None:
    """In a collection with a review workflow the item has no handle yet."""
    receipt = sword.DepositReceipt(
        edit_iri=f"http://localhost:8080/server/swordv2/edit/{NEW_UUID.upper()}",
        alternate_url="",
        identifiers=(),
    )
    assert save.saved_item_of(receipt, SETTINGS).cms_id == NEW_UUID


def test_dspace_down_is_unreachable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        msg = "refused"
        raise httpx2.ConnectError(msg, request=request)

    with pytest.raises(CMSUnreachableError):
        save.save_to_dspace(CARD, OME_ID, SETTINGS, httpx2.MockTransport(refuse))


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_error_from_the_search_is_unreachable(status: int) -> None:
    with pytest.raises(CMSUnreachableError):
        FakeDSpace(search_status=status).save()


@pytest.mark.parametrize("status", [400, 422, 500])
def test_another_error_from_the_search_is_a_refusal_not_unreachable(
    status: int,
) -> None:
    """DSpace did answer, so the route must not say "The CMS is unreachable"."""
    with pytest.raises(sword.SWORDError) as error:
        FakeDSpace(search_status=status).save()
    assert not isinstance(error.value, CMSUnreachableError)
    assert error.value.status == status


def test_a_refused_login_is_a_login_error() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if "discover" in request.url.path:
            return httpx2.Response(200, json=search_answer())
        return httpx2.Response(403)

    with pytest.raises(sword.SWORDLoginError):
        save.save_to_dspace(CARD, OME_ID, SETTINGS, httpx2.MockTransport(handle))


def test_saving_needs_the_website_for_the_item_page() -> None:
    with pytest.raises(RuntimeError, match="ui_url"):
        FakeDSpace().save(settings=replace(SETTINGS, ui_url=""))


def test_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "DSPACE_SERVER_URL": "http://dspace:8080/server/",
        "DSPACE_PUBLIC_SERVER_URL": "http://localhost:8080/server/",
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
    assert settings.deposit_host == "localhost:8080"


def test_the_public_server_url_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "DSPACE_SERVER_URL": "http://localhost:8080/server",
        "DSPACE_COLLECTION_HANDLE": "123456789/2",
        "DSPACE_DEPOSIT_EMAIL": "deposit@ome.test",
        "DSPACE_DEPOSIT_PASSWORD": "secret",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DSPACE_PUBLIC_SERVER_URL", raising=False)
    assert save.DepositSettings.from_env().deposit_host == ""


def test_missing_settings_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DSPACE_DEPOSIT_PASSWORD", raising=False)
    monkeypatch.setenv("DSPACE_SERVER_URL", "http://dspace:8080/server")
    with pytest.raises(RuntimeError, match="password"):
        save.DepositSettings.from_env()


def test_the_dspace_plugin_can_save() -> None:
    assert can_save(DSpacePlugin())


def test_save_resource_deposits_with_the_settings_and_the_website(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The item page uses the website that DSpace names (dspaceUI), like publishing."""
    calls: list[tuple] = []

    def fake_save(
        resource: EducationResource, ome_id: str, settings: save.DepositSettings
    ) -> SavedItem:
        calls.append((resource, ome_id, settings))
        return SavedItem(cms_id=NEW_UUID, cms_url=settings.item_url(NEW_UUID))

    for name, value in {
        "DSPACE_SERVER_URL": "http://dspace:8080/server",
        "DSPACE_COLLECTION_HANDLE": "123456789/2",
        "DSPACE_DEPOSIT_EMAIL": "deposit@ome.test",
        "DSPACE_DEPOSIT_PASSWORD": "secret",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(dspace_plugin, "ui_url", lambda _server: UI_URL)
    monkeypatch.setattr(dspace_plugin, "save_to_dspace", fake_save)

    saved = DSpacePlugin().save_resource(CARD, OME_ID)
    assert saved.cms_url == f"{UI_URL}/items/{NEW_UUID}"
    [(resource, ome_id, settings)] = calls
    assert (resource, ome_id) == (CARD, OME_ID)
    assert settings.ui_url == UI_URL
    assert settings.server_url == "http://dspace:8080/server"
