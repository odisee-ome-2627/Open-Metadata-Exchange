"""The real save route: fetch the card, call the plugin, map the errors.

The plugins here are fakes of a few lines. They only do what the contract of #227
promises: return a `SavedItem` or raise `CMSUnreachableError`. The DSpace plugin
(#223) is checked against the same route once it exists.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import ClassVar, override

import nntp
import pytest
from fastapi.testclient import TestClient

from server import ome_node
from server.main import app, current_plugin
from server.plugins.ome_plugin import (
    CMSUnreachableError,
    EducationResource,
    OMEPlugin,
    SavedItem,
)
from server.schemas import Attachment, NewsgroupPost, Post

CHANNEL = "ome.odisee"
SAVE_URL = f"/api/channel/{CHANNEL}/save"
MESSAGE_ID = "<abc123@ome-node-a.example.org>"
CARD = EducationResource(
    title="Mobile App Development, les 7",
    source_url="https://uce-odisee.be/ad/les07/theorie/",
)
client = TestClient(app)


def card_post(metadata: bytes | None = None) -> Post:
    return Post(
        id=7,
        message_id=MESSAGE_ID,
        channels=[CHANNEL],
        admin_contact="librarian@ome.test",
        subject=CARD.title,
        body="Summary",
        attachments=[
            Attachment(
                filename="metadata.json",
                mime_subtype="json",
                data=CARD.model_dump_json().encode() if metadata is None else metadata,
            )
        ],
        date=datetime(2026, 11, 21, tzinfo=UTC),
    )


class SavingPlugin(OMEPlugin):
    """Saves every card and remembers what it got."""

    calls: ClassVar[list[tuple[str, str]]] = []

    @override
    def save_resource(self, resource: EducationResource, ome_id: str) -> SavedItem:
        self.calls.append((resource.title, ome_id))
        return SavedItem(
            cms_id="123456789/42",
            cms_url="https://repo.example.org/handle/123456789/42",
        )


class UnreachablePlugin(OMEPlugin):
    @override
    def save_resource(self, resource: EducationResource, ome_id: str) -> SavedItem:
        raise CMSUnreachableError


def use(plugin: type[OMEPlugin]) -> Iterator[None]:
    app.dependency_overrides[current_plugin] = plugin
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def card_on_the_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ome_node, "get_post", lambda _channel, _number: card_post())


@pytest.fixture
def saving_plugin() -> Iterator[None]:
    SavingPlugin.calls.clear()
    yield from use(SavingPlugin)


@pytest.fixture
def unreachable_plugin() -> Iterator[None]:
    yield from use(UnreachablePlugin)


@pytest.mark.usefixtures("card_on_the_node", "saving_plugin")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post and the plugin")
def test_a_saved_card_gives_200_with_the_item_in_the_cms() -> None:
    response = client.post(SAVE_URL, json={"id": 7})
    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "status": "saved",
        "cms_id": "123456789/42",
        "cms_url": "https://repo.example.org/handle/123456789/42",
    }


@pytest.mark.usefixtures("card_on_the_node", "saving_plugin")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post and the plugin")
def test_the_plugin_gets_the_card_and_its_message_id() -> None:
    client.post(SAVE_URL, json={"id": 7})
    # pytest-run-parallel repeats the test without its fixtures: check the last call
    assert SavingPlugin.calls[-1] == (CARD.title, MESSAGE_ID)


@pytest.mark.usefixtures("card_on_the_node", "unreachable_plugin")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post and the plugin")
def test_an_unreachable_cms_gives_502() -> None:
    response = client.post(SAVE_URL, json={"id": 7})
    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"detail": "The CMS is unreachable"}


@pytest.mark.usefixtures("saving_plugin")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post and the plugin")
def test_a_missing_card_gives_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_channel: str, _number: int) -> Post:
        raise nntp.NNTPTemporaryError(423, "No such article number in this group")

    monkeypatch.setattr(ome_node, "get_post", missing)
    calls_before = len(SavingPlugin.calls)
    response = client.post(SAVE_URL, json={"id": 7})
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert len(SavingPlugin.calls) == calls_before  # the plugin was never called


@pytest.mark.usefixtures("saving_plugin")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post and the plugin")
def test_a_card_this_node_cannot_read_gives_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreadable = card_post(metadata=b'{"title": 5}')
    monkeypatch.setattr(ome_node, "get_post", lambda _channel, _number: unreadable)
    calls_before = len(SavingPlugin.calls)
    response = client.post(SAVE_URL, json={"id": 7})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "This card cannot be read by this node"}
    assert len(SavingPlugin.calls) == calls_before  # nothing half-empty is saved


def test_from_post_keeps_the_message_id() -> None:
    article = NewsgroupPost(
        id=7,
        channel=CHANNEL,
        headers={
            "Message-ID": MESSAGE_ID,
            "Subject": "Subject",
            "From": "librarian@ome.test",
            "Date": "Sat, 21 Nov 2026 20:00:00 +0100",
        },
        body="Body",
    )
    assert ome_node.from_post(article).message_id == MESSAGE_ID
