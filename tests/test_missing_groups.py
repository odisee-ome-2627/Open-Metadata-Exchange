"""One bad article or one missing group must not break the browse routes."""

from datetime import UTC, datetime
from http import HTTPStatus

import nntp
import pytest
from fastapi.testclient import TestClient

from server import ome_node, utils
from server.main import app
from server.schemas import Channel, ChannelSummary, NewsgroupPost, Post

client = TestClient(app)


def an_article(headers: dict[str, str]) -> NewsgroupPost:
    return NewsgroupPost(id=7, channel="ome.odisee", headers=headers, body="Body")


def test_an_article_without_a_date_gets_the_date_of_the_server() -> None:
    before = datetime.now(tz=UTC)
    post = ome_node.from_post(an_article({"Subject": "No date"}))
    assert before <= post.date <= datetime.now(tz=UTC)


def test_an_article_with_an_unreadable_date_gets_the_date_of_the_server() -> None:
    before = datetime.now(tz=UTC)
    post = ome_node.from_post(an_article({"Date": "not a date"}))
    assert before <= post.date <= datetime.now(tz=UTC)


def test_an_article_keeps_its_own_date() -> None:
    post = ome_node.from_post(an_article({"Date": "Sat, 21 Nov 2026 20:00:00 +0100"}))
    assert post.date == datetime(2026, 11, 21, 19, tzinfo=UTC)


@pytest.fixture
def only_the_first_group_on_inn(monkeypatch: pytest.MonkeyPatch) -> str:
    """INN has the group of the first plugin only. The others raise 411 like INN."""
    first, _description, plugin = next(iter(utils.get_channels()))
    monkeypatch.setattr(utils, "site_plugin", plugin)  # the site of this node
    monkeypatch.setattr(
        utils, "channels_on_inn", lambda: iter([Channel(name=first, description="")])
    )

    def group_must_exist(slug: str) -> None:
        if slug != first:
            raise nntp.NNTPTemporaryError(411, f"No such group {slug}")

    def summary(slug: str) -> ChannelSummary:
        group_must_exist(slug)
        return ChannelSummary(
            name=slug, estimated_total_articles=1, first_article=1, last_article=1
        )

    def last_posts(slug: str, _num: int = 3) -> list[Post]:
        group_must_exist(slug)
        return [
            Post(
                id=1,
                channels=[slug],
                admin_contact="librarian@ome.test",
                subject="A card",
                body="Body",
                attachments=[],
                date=datetime(2026, 11, 24, tzinfo=UTC),
            )
        ]

    monkeypatch.setattr(utils, "channel_summary", summary)
    monkeypatch.setattr(utils, "get_last_n_posts", last_posts)
    return first


def test_explore_skips_a_group_that_is_missing_on_inn(
    only_the_first_group_on_inn: str,
) -> None:
    response = client.get("/api/imls/v2/explore-oer-exchange/")
    assert response.status_code == HTTPStatus.OK
    channels = response.json()["sections"][1]["data"]
    assert [channel["slug"] for channel in channels] == [only_the_first_group_on_inn]


def test_browse_skips_a_group_that_is_missing_on_inn(
    only_the_first_group_on_inn: str,
) -> None:
    response = client.get("/api/imls/v2/collections/browse/")
    assert response.status_code == HTTPStatus.OK
    collections = response.json()["collections"]
    assert [item["channel"] for item in collections["items"]] == [
        only_the_first_group_on_inn
    ]
    channel_filter = collections["filters"][0]["items"]
    assert [item["slug"] for item in channel_filter] == [only_the_first_group_on_inn]
