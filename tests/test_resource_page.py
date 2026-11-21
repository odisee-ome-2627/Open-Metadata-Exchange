"""The resource page of a card and its route."""

from datetime import UTC, datetime
from http import HTTPStatus

import nntp
import pytest
from fastapi.testclient import TestClient

from server import ome_node, utils
from server.main import app
from server.plugins.ome_plugin import EducationResource
from server.schemas import Attachment, Post

CHANNEL = "ome.odisee"
PAGE_URL = f"/api/imls/v2/resources/{CHANNEL}/materials/7"
CARD = EducationResource(
    title="Mobile App Development, les 7",
    description="Offline-first tegenover cache en online-only.",
    authors=["Doe, Jane"],
    subject_tags=["offline-first"],
    source_url="https://uce-odisee.be/ad/les07/theorie/",
    spdx_license_expression="CC-BY-4.0",
)
client = TestClient(app)


def metadata_json(card: EducationResource) -> Attachment:
    return Attachment(
        filename="metadata.json",
        mime_subtype="json",
        data=card.model_dump_json().encode(),
    )


def a_post(*attachments: Attachment) -> Post:
    return Post(
        id=7,
        channels=[CHANNEL],
        admin_contact="librarian@ome.test",
        subject="Subject",
        body="Body",
        attachments=list(attachments),
        date=datetime(2026, 11, 21, tzinfo=UTC),
    )


def test_the_page_reads_the_card_in_metadata_json() -> None:
    page = utils.post_to_page(a_post(metadata_json(CARD)))
    resource = page.resource
    assert page.clientInfo.slug == CHANNEL
    assert (resource.id, resource.channel) == (7, CHANNEL)
    assert resource.title == CARD.title
    assert resource.abstract == CARD.description
    assert resource.authors == ["Doe, Jane"]
    assert resource.micrositeResourceURL == CARD.source_url
    assert resource.license_title == "CC-BY-4.0"
    assert resource.collections_titles == [CHANNEL]


def test_a_post_without_a_card_uses_its_subject_and_body() -> None:
    resource = utils.post_to_page(a_post()).resource
    assert (resource.title, resource.abstract) == ("Subject", "Body")


def test_summary_and_details_carry_the_channel() -> None:
    post = a_post(metadata_json(CARD))
    assert utils.post_to_summary(post).channel == CHANNEL
    details = utils.post_to_details(post)
    assert details.channel == CHANNEL
    assert details.detailURL == PAGE_URL


@pytest.fixture
def card_on_the_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ome_node, "get_post", lambda _channel, _article: a_post(metadata_json(CARD))
    )


@pytest.fixture
def no_such_article(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_channel: str, _article: int) -> Post:
        raise nntp.NNTPTemporaryError(423, "No such article number in this group")

    monkeypatch.setattr(ome_node, "get_post", missing)


@pytest.mark.usefixtures("card_on_the_node")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post for every thread")
def test_the_route_gives_the_page_with_the_channel_and_number() -> None:
    response = client.get(PAGE_URL)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["resource"]["channel"] == CHANNEL
    assert body["resource"]["id"] == 7
    assert body["clientInfo"]["slug"] == CHANNEL


@pytest.mark.usefixtures("no_such_article")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post for every thread")
def test_a_missing_article_gives_404() -> None:
    response = client.get(PAGE_URL)
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"detail": f"Card 7 not found in {CHANNEL}"}


def test_an_unknown_channel_gives_404_from_inn() -> None:
    response = client.get("/api/imls/v2/resources/ome.does-not-exist/materials/1")
    assert response.status_code == HTTPStatus.NOT_FOUND
