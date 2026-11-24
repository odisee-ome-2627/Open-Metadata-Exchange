"""Tests for the generic SWORD v2 writer, with fake answers instead of a repository."""

import xml.etree.ElementTree as ET

import httpx2
import pytest

from server import sword
from server.plugins.ome_plugin import CMSUnreachableError

COLLECTION_URL = "http://repository.test/swordv2/collection/1/2"
AUTH = ("deposit@ome.test", "secret")
COLLECTION = sword.SWORDCollection(COLLECTION_URL, AUTH)
RECEIPT = b"""<?xml version="1.0"?>
<entry xmlns="http://www.w3.org/2005/Atom" xmlns:dcterms="http://purl.org/dc/terms/">
  <id>http://repository.test/handle/1/9</id>
  <link rel="edit" href="http://repository.test/swordv2/edit/abc"/>
  <link rel="alternate" href="http://repository.test/handle/1/9"/>
  <dcterms:identifier>http://repository.test/handle/1/9</dcterms:identifier>
</entry>"""
ERROR_DOCUMENT = b"""<sword:error xmlns="http://www.w3.org/2005/Atom"
    xmlns:sword="http://purl.org/net/sword/">
  <title>ERROR</title><summary>The collection does not take this</summary>
</sword:error>"""


def answer(status: int, content: bytes = b"", **headers: str) -> httpx2.MockTransport:
    return httpx2.MockTransport(
        lambda _request: httpx2.Response(status, content=content, headers=headers)
    )


def test_an_entry_has_one_dcterms_element_per_term_in_order() -> None:
    content = sword.atom_entry([("title", "T"), ("creator", "A"), ("creator", "B")])
    entry = ET.fromstring(content)  # noqa: S314
    terms = [(el.tag.split("}")[1], el.text) for el in entry if "dc/terms" in el.tag]
    assert terms == [("title", "T"), ("creator", "A"), ("creator", "B")]


def test_the_first_title_is_also_the_atom_title() -> None:
    entry = ET.fromstring(sword.atom_entry([("title", "T"), ("title", "Other")]))  # noqa: S314
    assert entry.findtext("atom:title", namespaces=sword.NS) == "T"


def test_empty_values_are_left_out() -> None:
    entry = ET.fromstring(sword.atom_entry([("title", "T"), ("abstract", "")]))  # noqa: S314
    assert entry.find("dcterms:abstract", sword.NS) is None


def test_a_deposit_posts_the_entry_with_basic_auth_and_in_progress_false() -> None:
    requests: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(201, content=RECEIPT)

    entry = sword.atom_entry([("title", "T")])
    sword.deposit_entry(COLLECTION, entry, transport=httpx2.MockTransport(record))
    [request] = requests
    assert (request.method, str(request.url)) == ("POST", COLLECTION_URL)
    assert request.headers["In-Progress"] == "false"
    assert request.headers["Content-Type"] == sword.ENTRY_CONTENT_TYPE
    assert request.headers["Authorization"].startswith("Basic ")
    assert request.content == entry


def test_a_deposit_can_carry_another_host_header() -> None:
    requests: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(201, content=RECEIPT)

    sword.deposit_entry(
        sword.SWORDCollection(COLLECTION_URL, AUTH, host="public.repo.test:8080"),
        sword.atom_entry([("title", "T")]),
        transport=httpx2.MockTransport(record),
    )
    [request] = requests
    assert str(request.url) == COLLECTION_URL
    assert request.headers["Host"] == "public.repo.test:8080"


def test_a_deposit_can_be_left_in_progress() -> None:
    requests: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(201, content=RECEIPT)

    sword.deposit_entry(
        COLLECTION,
        b"<entry/>",
        in_progress=True,
        transport=httpx2.MockTransport(record),
    )
    [request] = requests
    assert request.headers["In-Progress"] == "true"


def test_the_receipt_gives_the_edit_iri_page_and_identifiers() -> None:
    receipt = sword.deposit_entry(
        COLLECTION, b"<entry/>", transport=answer(201, RECEIPT)
    )
    assert receipt.edit_iri == "http://repository.test/swordv2/edit/abc"
    assert receipt.alternate_url == "http://repository.test/handle/1/9"
    assert "http://repository.test/handle/1/9" in receipt.identifiers


def test_without_an_edit_link_the_location_header_is_the_edit_iri() -> None:
    receipt = sword.parse_receipt(
        b'<entry xmlns="http://www.w3.org/2005/Atom"/>', "http://x/edit/1"
    )
    assert receipt.edit_iri == "http://x/edit/1"


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_login_raises_a_login_error(status: int) -> None:
    with pytest.raises(sword.SWORDLoginError):
        sword.deposit_entry(COLLECTION, b"", transport=answer(status))


def test_another_refusal_carries_the_summary_of_the_error_document() -> None:
    with pytest.raises(sword.SWORDError, match="does not take this") as error:
        sword.deposit_entry(COLLECTION, b"", transport=answer(415, ERROR_DOCUMENT))
    assert error.value.status == 415


def test_a_repository_that_does_not_answer_is_unreachable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        msg = "refused"
        raise httpx2.ConnectError(msg, request=request)

    with pytest.raises(CMSUnreachableError):
        sword.deposit_entry(COLLECTION, b"", transport=httpx2.MockTransport(refuse))


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_error_is_unreachable(status: int) -> None:
    with pytest.raises(sword.SWORDUnreachableError):
        sword.deposit_entry(COLLECTION, b"", transport=answer(status))


def test_the_writer_has_no_dspace_names() -> None:
    code = sword.__loader__.get_source(sword.__name__) or ""
    docstring = sword.__doc__ or ""
    assert "dspace" not in code.replace(docstring, "").lower()
