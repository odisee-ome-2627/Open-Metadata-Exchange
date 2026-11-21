"""Tests for the generic OAI-PMH reader, with fake answers instead of a repository."""

from datetime import UTC, datetime

import pytest

from server import oai_pmh

BASE_URL = "http://repository.test/oai/request"


def page(*records: str, next_page: str | None = None) -> bytes:
    """A ListRecords answer with the given records and an optional resumption token."""
    resumption = (
        f"<resumptionToken>{next_page}</resumptionToken>"
        if next_page is not None
        else ""
    )
    return (
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>'
        f"{''.join(records)}{resumption}</ListRecords></OAI-PMH>"
    ).encode()


def record(identifier: str, *, deleted: bool = False) -> str:
    status = ' status="deleted"' if deleted else ""
    metadata = "" if deleted else "<metadata><title>x</title></metadata>"
    return (
        f"<record><header{status}><identifier>{identifier}</identifier>"
        f"<datestamp>2026-11-20T10:18:48Z</datestamp></header>{metadata}</record>"
    )


def error(code: str) -> bytes:
    return (
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        f'<error code="{code}">Something about {code}</error></OAI-PMH>'
    ).encode()


class FakeRepository:
    """Stands in for the HTTP call: hands out `answers` and remembers `requests`."""

    def __init__(self) -> None:
        self.answers: list[bytes] = []
        self.requests: list[dict[str, str]] = []

    def fetch(self, base_url: str, params: dict[str, str]) -> bytes:
        assert base_url == BASE_URL
        self.requests.append(params)
        return self.answers.pop(0)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepository:
    fake = FakeRepository()
    monkeypatch.setattr(oai_pmh, "_fetch", fake.fetch)
    return fake


def identifiers(records: list) -> list[str]:
    return [
        r.findtext("oai:header/oai:identifier", "", oai_pmh.OAI_NS) for r in records
    ]


def test_list_records_follows_the_resumption_token(repo: FakeRepository) -> None:
    repo.answers = [
        page(record("oai:x:1"), record("oai:x:2"), next_page="page-2"),
        page(record("oai:x:3"), next_page=""),  # an empty token means: last page
    ]
    records = list(oai_pmh.list_records(BASE_URL, "dim", set_spec="col_1_2"))
    assert identifiers(records) == ["oai:x:1", "oai:x:2", "oai:x:3"]
    assert repo.requests == [
        {"verb": "ListRecords", "metadataPrefix": "dim", "set": "col_1_2"},
        {"verb": "ListRecords", "resumptionToken": "page-2"},
    ]


def test_list_records_stops_without_a_token(repo: FakeRepository) -> None:
    repo.answers = [page(record("oai:x:1"))]
    assert identifiers(list(oai_pmh.list_records(BASE_URL, "dim"))) == ["oai:x:1"]
    assert len(repo.requests) == 1


def test_list_records_skips_deleted_records(repo: FakeRepository) -> None:
    repo.answers = [page(record("oai:x:1"), record("oai:x:2", deleted=True))]
    assert identifiers(list(oai_pmh.list_records(BASE_URL, "dim"))) == ["oai:x:1"]


def test_list_records_can_include_deleted_records(repo: FakeRepository) -> None:
    repo.answers = [page(record("oai:x:1"), record("oai:x:2", deleted=True))]
    records = list(oai_pmh.list_records(BASE_URL, "dim", include_deleted=True))
    assert identifiers(records) == ["oai:x:1", "oai:x:2"]
    assert oai_pmh.is_deleted(records[1])


def test_list_records_with_nothing_new_yields_nothing(repo: FakeRepository) -> None:
    repo.answers = [error("noRecordsMatch")]
    assert list(oai_pmh.list_records(BASE_URL, "dim", from_date="2026-11-20")) == []
    assert repo.requests[0]["from"] == "2026-11-20"


def test_list_records_formats_a_datetime_as_utc(repo: FakeRepository) -> None:
    repo.answers = [error("noRecordsMatch")]
    since = datetime(2026, 11, 20, 11, 18, 48, tzinfo=UTC)
    list(oai_pmh.list_records(BASE_URL, "dim", from_date=since))
    assert repo.requests[0]["from"] == "2026-11-20T11:18:48Z"


def test_list_records_raises_other_errors(repo: FakeRepository) -> None:
    repo.answers = [error("badArgument")]
    with pytest.raises(oai_pmh.OAIPMHError) as raised:
        list(oai_pmh.list_records(BASE_URL, "dim"))
    assert raised.value.code == "badArgument"


def test_get_record(repo: FakeRepository) -> None:
    repo.answers = [
        (
            '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord>'
            f"{record('oai:x:3')}</GetRecord></OAI-PMH>"
        ).encode()
    ]
    found = oai_pmh.get_record(BASE_URL, "oai:x:3", "dim")
    assert identifiers([found]) == ["oai:x:3"]
    assert repo.requests == [
        {"verb": "GetRecord", "identifier": "oai:x:3", "metadataPrefix": "dim"}
    ]


def test_get_record_unknown_identifier(repo: FakeRepository) -> None:
    repo.answers = [error("idDoesNotExist")]
    with pytest.raises(oai_pmh.OAIPMHError) as raised:
        oai_pmh.get_record(BASE_URL, "oai:x:404", "dim")
    assert raised.value.code == "idDoesNotExist"
