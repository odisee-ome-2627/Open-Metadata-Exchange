"""The real save route: fetch the card, call the plugin, map the errors.

The plugins here are fakes of a few lines. They only do what the contract of #227
promises: return a `SavedItem`, or raise `CardAlreadySavedError` or
`CMSUnreachableError`. The DSpace plugin has its own tests in
`test_dspace_save.py`. One test here runs it against the route while DSpace is down.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import ClassVar, override

import httpx2
import nntp
import pytest
from fastapi.testclient import TestClient

from server import ome_node
from server.main import app, current_plugin
from server.plugins.dspace import plugin as dspace_plugin
from server.plugins.dspace.plugin import DSpacePlugin
from server.plugins.ome_plugin import (
    CardAlreadySavedError,
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


EXISTING = SavedItem(
    cms_id="6bb452d9-d7e5-4f34-a534-630dbb6d4d31",
    cms_url="https://repo.example.org/items/6bb452d9-d7e5-4f34-a534-630dbb6d4d31",
)


class AlreadySavedPlugin(OMEPlugin):
    """The CMS already has every card, so nothing new is saved."""

    calls: ClassVar[list[str]] = []

    @override
    def save_resource(self, resource: EducationResource, ome_id: str) -> SavedItem:
        self.calls.append(ome_id)
        raise CardAlreadySavedError(EXISTING)


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
def already_saved_plugin() -> Iterator[None]:
    AlreadySavedPlugin.calls.clear()
    yield from use(AlreadySavedPlugin)


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


@pytest.mark.usefixtures("card_on_the_node", "already_saved_plugin")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post and the plugin")
def test_a_card_the_cms_already_has_gives_409_with_the_existing_item() -> None:
    """The button shows "In my library" and links to `cms_url` (#325)."""
    response = client.post(SAVE_URL, json={"id": 7})
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {"detail": EXISTING.model_dump()}
    # The plugin was asked once and saved nothing new
    assert AlreadySavedPlugin.calls[-1] == MESSAGE_ID


@pytest.mark.usefixtures("card_on_the_node", "unreachable_plugin")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post and the plugin")
def test_an_unreachable_cms_gives_502() -> None:
    response = client.post(SAVE_URL, json={"id": 7})
    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"detail": "The CMS is unreachable"}


@pytest.fixture
def dspace_stopped(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The real DSpace plugin, with every setting, while DSpace does not answer."""

    def no_answer(url: str, **_kwargs: object) -> httpx2.Response:
        msg = "Name or service not known"
        raise httpx2.ConnectError(msg, request=httpx2.Request("GET", url))

    monkeypatch.setenv("DSPACE_SERVER_URL", "http://dspace:8080/server")
    monkeypatch.setenv("DSPACE_COLLECTION_HANDLE", "123456789/2")
    monkeypatch.setenv("DSPACE_DEPOSIT_EMAIL", "deposit@ome.test")
    monkeypatch.setenv("DSPACE_DEPOSIT_PASSWORD", "secret")
    monkeypatch.setattr(httpx2, "get", no_answer)
    dspace_plugin.ui_url.cache_clear()
    yield from use(DSpacePlugin)
    dspace_plugin.ui_url.cache_clear()


@pytest.mark.usefixtures("card_on_the_node", "dspace_stopped")
@pytest.mark.thread_unsafe(reason="replaces ome_node.get_post, the plugin and httpx2")
def test_saving_to_a_stopped_dspace_gives_502() -> None:
    """The first call to DSpace asks its website (dspaceUI): that must give 502 too."""
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
