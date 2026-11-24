"""Tests for the DSpace bulk import, with fake OAI-PMH answers instead of DSpace."""

import pytest

from server import oai_pmh
from server.plugins.dspace import bulk_import

OAI_URL = "http://dspace.test/server/oai/request"


def dim_record(handle: str, title: str, *, deleted: bool = False) -> str:
    status = ' status="deleted"' if deleted else ""
    metadata = (
        ""
        if deleted
        else (
            '<metadata><dim:dim xmlns:dim="http://www.dspace.org/xmlns/dspace/dim">'
            f'<dim:field mdschema="dc" element="title">{title}</dim:field>'
            '<dim:field mdschema="dc" element="rights" qualifier="uri">'
            "https://creativecommons.org/licenses/by/4.0/</dim:field>"
            "</dim:dim></metadata>"
        )
    )
    return (
        f"<record><header{status}><identifier>oai:dspace.test:{handle}</identifier>"
        "<datestamp>2026-11-20T10:18:48Z</datestamp>"
        "<setSpec>col_123456789_2</setSpec></header>"
        f"{metadata}</record>"
    )


def page(*records: str, next_page: str = "") -> bytes:
    return (
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>'
        f"{''.join(records)}<resumptionToken>{next_page}</resumptionToken>"
        "</ListRecords></OAI-PMH>"
    ).encode()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Answer two pages of a collection and remember the requests."""
    answers = [
        page(
            dim_record("123456789/3", "First"),
            dim_record("123456789/4", "Gone", deleted=True),
            next_page="token-2",
        ),
        page(dim_record("123456789/5", "Second")),
    ]
    requests: list[dict[str, str]] = []

    def fake_fetch(base_url: str, params: dict[str, str]) -> bytes:
        assert base_url == OAI_URL
        requests.append(params)
        return answers.pop(0)

    monkeypatch.setattr(oai_pmh, "_fetch", fake_fetch)
    return requests


def test_collection_set() -> None:
    assert bulk_import.collection_set("123456789/2") == "col_123456789_2"


def test_bulk_import_reads_every_page(sent: list[dict[str, str]]) -> None:
    cards = list(bulk_import.bulk_import(OAI_URL, "123456789/2"))
    # The deleted item on page 1 is skipped.
    assert [card.title for card in cards] == ["First", "Second"]
    assert all(card.spdx_license_expression == "CC-BY-4.0" for card in cards)
    assert sent == [
        {"verb": "ListRecords", "metadataPrefix": "dim", "set": "col_123456789_2"},
        {"verb": "ListRecords", "resumptionToken": "token-2"},
    ]


def test_read_items_only_what_changed(sent: list[dict[str, str]]) -> None:
    items = list(bulk_import.read_items(OAI_URL, "123456789/2", since="2026-11-20"))
    assert [item.handle for item in items] == ["123456789/3", "123456789/5"]
    assert sent[0]["from"] == "2026-11-20"


def test_settings_come_from_the_environment(
    sent: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DSPACE_OAI_URL", OAI_URL)
    monkeypatch.setenv("DSPACE_COLLECTION_HANDLE", "123456789/2")
    assert len(list(bulk_import.bulk_import())) == 2
    assert sent[0]["set"] == "col_123456789_2"


@pytest.mark.usefixtures("sent")
def test_the_oai_url_can_come_from_the_server_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DSPACE_OAI_URL", raising=False)
    monkeypatch.setenv("DSPACE_SERVER_URL", OAI_URL.removesuffix("/oai/request"))
    monkeypatch.setenv("DSPACE_COLLECTION_HANDLE", "123456789/2")
    assert len(list(bulk_import.bulk_import())) == 2


def test_missing_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DSPACE_OAI_URL", raising=False)
    monkeypatch.delenv("DSPACE_SERVER_URL", raising=False)
    monkeypatch.delenv("DSPACE_COLLECTION_HANDLE", raising=False)
    with pytest.raises(RuntimeError, match="DSPACE_OAI_URL"):
        list(bulk_import.bulk_import())
