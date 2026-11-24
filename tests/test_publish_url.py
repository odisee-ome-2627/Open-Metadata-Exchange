from collections.abc import Callable
from http import HTTPStatus
from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient
from nntp import NNTPTemporaryError

from server import ome_node
from server.main import app
from server.plugins.dspace.plugin import DSpacePlugin
from server.plugins.ome_plugin import (
    CMSUnreachableError,
    EducationResource,
    ItemNotFoundError,
    NotAnItemLinkError,
)
from server.schemas import Post

ITEM_URL = "http://localhost:4001/handle/123456789/3"


class FakeDSpacePlugin(DSpacePlugin):
    """The DSpace plugin, but with a fixed card instead of a call to DSpace."""

    # The real newsgroup comes from DSPACE_NEWSGROUP, which the tests do not set.
    newsgroups: dict[str, str] = MappingProxyType({"ome.odisee": "Test newsgroup"})

    def make_metadata_card_from_url(self, url: str) -> EducationResource:
        assert url == ITEM_URL
        return EducationResource(title="Introduction to AI", source_url=url)


@pytest.fixture
def inn_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[Post], str | bool]], list[Post]]:
    """Replace the plugin, and let each test decide how INN answers a post."""

    def use(answer: Callable[[Post], str | bool]) -> list[Post]:
        sent: list[Post] = []

        def fake_create_post(post: Post) -> str | bool:
            sent.append(post)
            return answer(post)

        monkeypatch.setattr(ome_node, "plugin", FakeDSpacePlugin())
        monkeypatch.setattr(ome_node, "create_post", fake_create_post)
        return sent

    return use


def test_publish_url_answers_true_when_inn_returns_a_message_id(
    inn_answers: Callable,
) -> None:
    posts = inn_answers(lambda _post: "<abc123@nntp.example>")
    response = TestClient(app).post("/api/publish_url", json={"url": ITEM_URL})
    assert response.status_code == HTTPStatus.OK
    assert response.json() is True
    assert len(posts) == 1
    assert posts[0].subject == "Introduction to AI"
    assert list(posts[0].channels) == ["ome.odisee"]


def test_publish_url_answers_true_when_inn_returns_no_message_id(
    inn_answers: Callable,
) -> None:
    """Without a Message-ID in INN's reply, pynntp returns True."""
    inn_answers(lambda _post: True)
    response = TestClient(app).post("/api/publish_url", json={"url": ITEM_URL})
    assert response.status_code == HTTPStatus.OK
    assert response.json() is True


def test_publish_url_fails_when_inn_refuses_the_post(inn_answers: Callable) -> None:
    """A refused post raises in pynntp, so the route must not answer true."""

    def refuse(_post: Post) -> str:
        raise NNTPTemporaryError(441, "Posting failed")

    posts = inn_answers(refuse)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/publish_url", json={"url": ITEM_URL})
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.text != "true"
    assert len(posts) == 1  # it did try to post


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotAnItemLinkError("This link is not an item of the Odisee DSpace"), 400),
        (ItemNotFoundError("This item is not in OAI-PMH yet."), 404),
    ],
)
def test_publish_url_explains_a_bad_link(
    inn_answers: Callable,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
) -> None:
    """A wrong or unknown link is the librarian's to fix: 400/404 with the reason."""

    def refuse(_url: str) -> EducationResource:
        raise error

    posts = inn_answers(lambda _post: True)
    monkeypatch.setattr(ome_node.plugin, "make_metadata_card_from_url", refuse)
    response = TestClient(app).post("/api/publish_url", json={"url": ITEM_URL})
    assert response.status_code == status
    assert response.json() == {"detail": str(error)}
    assert posts == []  # nothing reaches INN


def test_publish_url_answers_502_when_the_cms_is_unreachable(
    inn_answers: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DSpace is down: 502 like the save route, so the librarian can try again."""

    def unreachable(_url: str) -> EducationResource:
        msg = "DSpace at http://dspace:8080/server/api did not answer"
        raise CMSUnreachableError(msg)

    posts = inn_answers(lambda _post: True)
    monkeypatch.setattr(ome_node.plugin, "make_metadata_card_from_url", unreachable)
    response = TestClient(app).post("/api/publish_url", json={"url": ITEM_URL})
    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"detail": "The CMS is unreachable"}
    assert posts == []  # nothing reaches INN
