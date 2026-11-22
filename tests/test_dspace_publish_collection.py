"""Tests for publishing a DSpace collection: what is posted to the node, and once."""

from pathlib import Path

import pytest

from server.plugins.dspace import publish_collection
from server.plugins.dspace.dspace_models import DimField, DSpaceItem

HANDLE_URL = "http://localhost:4001/handle"


def item(handle: str, *, source: str = "", uri: bool = True) -> DSpaceItem:
    """A DSpace item with a title, and optionally dc.source and dc.identifier.uri."""
    fields = [DimField(mdschema="dc", element="title", value=f"Item {handle}")]
    if uri:
        fields.append(
            DimField(
                mdschema="dc",
                element="identifier",
                qualifier="uri",
                value=f"{HANDLE_URL}/{handle}",
            )
        )
    if source:
        fields.append(DimField(mdschema="dc", element="source", value=source))
    return DSpaceItem(identifier=f"oai:localhost:{handle}", fields=fields)


@pytest.mark.parametrize(
    ("dspace_item", "published", "reason"),
    [
        pytest.param(item("1/1"), set(), None, id="new"),
        pytest.param(item("1/1"), {"1/1"}, "already published", id="published"),
        pytest.param(
            item("1/1", source="<abc.123@nntp.ome.odisee.be>"),
            set(),
            "saved from OME",
            id="saved-from-ome",
        ),
        pytest.param(
            item("1/1", source=" <abc.123@nntp.ome.odisee.be>\n"),
            set(),
            "saved from OME",
            id="saved-from-ome-with-spaces",
        ),
        # dc.source is ordinary Dublin Core: a library can put any source in it.
        pytest.param(
            item("1/1", source="Journal of Open Education, 12(3)"),
            set(),
            None,
            id="other-source",
        ),
        pytest.param(
            item("1/1", uri=False), set(), "no dc.identifier.uri", id="no-uri"
        ),
    ],
)
def test_reason_to_skip(
    dspace_item: DSpaceItem, published: set[str], reason: str | None
) -> None:
    assert publish_collection.reason_to_skip(dspace_item, published) == reason


def test_state_file_round_trip(tmp_path: Path) -> None:
    state = tmp_path / "published.json"
    assert publish_collection.load_published(state) == set()
    publish_collection.save_published(state, {"1/2", "1/1"})
    assert publish_collection.load_published(state) == {"1/1", "1/2"}
    assert not state.with_name("published.json.tmp").exists()


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the call to the node and remember the URLs it was asked to publish."""
    urls: list[str] = []

    def fake_publish(_client: object, _api_url: str, item_url: str) -> None:
        urls.append(item_url)

    monkeypatch.setattr(publish_collection, "publish", fake_publish)
    return urls


def test_second_run_posts_nothing(tmp_path: Path, posted: list[str]) -> None:
    state = tmp_path / "published.json"
    collection = [
        item("1/1"),
        item("1/2", source="<abc.123@nntp.ome.odisee.be>"),
        item("1/3"),
    ]

    assert publish_collection.publish_items(collection, state) == 2
    assert posted == [f"{HANDLE_URL}/1/1", f"{HANDLE_URL}/1/3"]

    posted.clear()
    assert publish_collection.publish_items(collection, state) == 0
    assert posted == []


def test_new_item_is_published_on_the_next_run(
    tmp_path: Path, posted: list[str]
) -> None:
    state = tmp_path / "published.json"
    publish_collection.publish_items([item("1/1")], state)
    posted.clear()

    publish_collection.publish_items([item("1/1"), item("1/4")], state)
    assert posted == [f"{HANDLE_URL}/1/4"]


def test_dry_run_counts_but_posts_and_saves_nothing(
    tmp_path: Path, posted: list[str]
) -> None:
    state = tmp_path / "published.json"
    assert publish_collection.publish_items([item("1/1")], state, dry_run=True) == 1
    assert posted == []
    assert not state.exists()


def test_a_refused_item_is_not_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the node refuses an item, the next run tries it again."""
    state = tmp_path / "published.json"

    def refuse(_client: object, _api_url: str, item_url: str) -> None:
        if item_url.endswith("/1/2"):
            msg = "refused"
            raise RuntimeError(msg)

    monkeypatch.setattr(publish_collection, "publish", refuse)
    with pytest.raises(RuntimeError, match="refused"):
        publish_collection.publish_items([item("1/1"), item("1/2")], state)
    assert publish_collection.load_published(state) == {"1/1"}
