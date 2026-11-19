"""Tests for `scripts/seed_dspace.py`, without a running DSpace."""

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.append(str(SCRIPTS))  # seed_dspace imports dspace_bootstrap
_spec = importlib.util.spec_from_file_location(
    "seed_dspace", SCRIPTS / "seed_dspace.py"
)
assert _spec
assert _spec.loader
seed_dspace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_dspace)

BASE = "http://dspace.test/server"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
EPUB_PART = b'filename="book.epub"\r\nContent-Type: application/epub+zip'
NEW_ITEM = {"uuid": "item-1", "handle": "123456789/9"}
MAKE_CLIENT = seed_dspace.make_client  # main() tests replace it in the module

Handler = Callable[[httpx2.Request], httpx2.Response]


def seed_item(seed_id: str, **fields: str) -> object:
    return seed_dspace.SeedItem(
        id=seed_id, title=seed_id.upper(), licence=CC_BY, **fields
    )


def write_manifest(folder: Path, items: list[object]) -> Path:
    (folder / "manifest.json").write_text(
        json.dumps({"items": items}), encoding="utf-8"
    )
    return folder


def search_page(identifiers: list[str | None], total_pages: int) -> dict:
    """An answer of the DSpace search, one item per value of dc.identifier.other."""
    objects = [
        {
            "_embedded": {
                "indexableObject": {
                    "metadata": {}
                    if identifier is None
                    else {"dc.identifier.other": [{"value": identifier}]}
                }
            }
        }
        for identifier in identifiers
    ]
    return {
        "_embedded": {
            "searchResult": {
                "_embedded": {"objects": objects},
                "page": {"totalPages": total_pages},
            }
        }
    }


def fake_dspace(existing: list[str | None], calls: list[str]) -> Handler:
    """A DSpace that knows the given items, creates new ones and logs every call."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if path.endswith("/authn/login"):
            return httpx2.Response(200, headers={"Authorization": "Bearer token"})
        if path.endswith("/security/csrf"):
            return httpx2.Response(204)
        if path.endswith("/core/items"):
            return httpx2.Response(201, json=NEW_ITEM)
        if path.endswith("/bundles"):
            return httpx2.Response(201, json={"uuid": "bundle-1"})
        if request.method == "DELETE":
            return httpx2.Response(204)
        return httpx2.Response(200, json=search_page(existing, total_pages=1))

    return handle


def client_for(handle: Handler) -> httpx2.Client:
    return MAKE_CLIENT(BASE, transport=httpx2.MockTransport(handle))


# --- the manifest ------------------------------------------------------------


def test_the_open_set_in_git_is_valid() -> None:
    assert len(seed_dspace.read_manifest(seed_dspace.OPEN_SET)) >= 5


@pytest.mark.parametrize("missing", ["id", "title", "licence"])
def test_an_item_needs_an_id_a_title_and_a_licence(
    tmp_path: Path, missing: str
) -> None:
    raw = {"id": "a", "title": "A", "licence": CC_BY}
    del raw[missing]
    with pytest.raises(SystemExit, match=f"needs {missing}"):
        seed_dspace.read_manifest(write_manifest(tmp_path, [raw]))


def test_a_seed_id_may_appear_only_once(tmp_path: Path) -> None:
    items = [
        {"id": "a", "title": "A", "licence": CC_BY},
        {"id": "a", "title": "B", "licence": CC_BY},
    ]
    with pytest.raises(SystemExit, match="appears twice"):
        seed_dspace.read_manifest(write_manifest(tmp_path, items))


def test_an_unknown_field_stops_the_script(tmp_path: Path) -> None:
    raw = {"id": "a", "title": "A", "licence": CC_BY, "publisher": "X"}
    with pytest.raises(SystemExit, match="unknown fields: publisher"):
        seed_dspace.read_manifest(write_manifest(tmp_path, [raw]))


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("id", 7, "id must be text"),
        ("authors", "Doe, Jane", "authors must be a list of texts"),
        ("subjects", ["Python", 3], "subjects must be a list of texts"),
    ],
)
def test_a_field_of_the_wrong_type_stops_the_script(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    raw = {"id": "a", "title": "A", "licence": CC_BY, field: value}
    with pytest.raises(SystemExit, match=error):
        seed_dspace.read_manifest(write_manifest(tmp_path, [raw]))


def test_a_missing_file_stops_the_script(tmp_path: Path) -> None:
    raw = {"id": "a", "title": "A", "licence": CC_BY, "file": "missing.epub"}
    with pytest.raises(SystemExit, match="does not exist"):
        seed_dspace.read_manifest(write_manifest(tmp_path, [raw]))


def test_metadata_has_the_seed_id_and_no_empty_fields() -> None:
    metadata = seed_dspace.to_metadata(seed_item("a", authors=("Doe, Jane",)))
    assert metadata["dc.identifier.other"] == [{"value": "ome-seed:a"}]
    assert metadata["dc.contributor.author"] == [{"value": "Doe, Jane"}]
    assert metadata["dc.rights.uri"] == [{"value": CC_BY}]
    assert "dc.subject" not in metadata
    assert "dc.relation.uri" not in metadata


def test_a_link_goes_into_dc_relation_and_never_into_dc_source() -> None:
    metadata = seed_dspace.to_metadata(seed_item("a", url="https://example.org/a"))
    assert metadata["dc.relation.uri"] == [{"value": "https://example.org/a"}]
    assert not any(key.startswith("dc.source") for key in metadata)


# --- talking to DSpace -------------------------------------------------------


def test_existing_seed_ids_reads_every_page_and_only_seed_ids() -> None:
    pages = {
        0: search_page(["ome-seed:a", None], total_pages=2),
        1: search_page(["<message-id@ome>", "ome-seed:b"], total_pages=2),
    }

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["scope"] == "col-1"
        return httpx2.Response(200, json=pages[int(request.url.params["page"])])

    with client_for(handle) as client:
        assert seed_dspace.existing_seed_ids(client, "col-1") == {"a", "b"}


def test_an_empty_collection_gets_every_item() -> None:
    posted: list[dict] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx2.Response(201, json=NEW_ITEM)
        return httpx2.Response(200, json=search_page([], total_pages=0))

    with client_for(handle) as client:
        items = [seed_item("a"), seed_item("b")]
        created = seed_dspace.seed(client, "col-1", items, seed_dspace.OPEN_SET)
    assert [item.id for item, _ in created] == ["a", "b"]
    assert [body["name"] for body in posted] == ["A", "B"]


def test_a_second_run_sends_no_post() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET", "the second run must not create anything"
        return httpx2.Response(
            200, json=search_page(["ome-seed:a", "ome-seed:b"], total_pages=1)
        )

    with client_for(handle) as client:
        items = [seed_item("a"), seed_item("b")]
        assert seed_dspace.seed(client, "col-1", items, seed_dspace.OPEN_SET) == []


def test_an_item_with_a_file_gets_it_uploaded(tmp_path: Path) -> None:
    (tmp_path / "book.epub").write_bytes(b"epub")
    calls: list[str] = []
    fake = fake_dspace([], calls)

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/bitstreams"):
            assert EPUB_PART in request.content
            calls.append(f"{request.method} {request.url.path}")
            return httpx2.Response(201, json={"uuid": "bitstream-1"})
        return fake(request)

    with client_for(handle) as client:
        seed_dspace.seed(client, "col-1", [seed_item("a", file="book.epub")], tmp_path)
    assert calls[-2:] == [
        "POST /server/api/core/items/item-1/bundles",
        "POST /server/api/core/bundles/bundle-1/bitstreams",
    ]


def test_a_failed_upload_removes_the_item_so_a_next_run_retries(
    tmp_path: Path,
) -> None:
    (tmp_path / "book.epub").write_bytes(b"epub")
    calls: list[str] = []
    fake = fake_dspace([], calls)

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/bitstreams"):
            return httpx2.Response(413)  # the file is too large
        return fake(request)

    with client_for(handle) as client, pytest.raises(httpx2.HTTPStatusError):
        seed_dspace.seed(client, "col-1", [seed_item("a", file="book.epub")], tmp_path)
    assert calls[-1] == "DELETE /server/api/core/items/item-1"


# --- main --------------------------------------------------------------------


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    existing: list[str | None],
    *flags: str,
) -> list[str]:
    """Run main() against a fake DSpace and return the calls, `oai import` included."""
    calls: list[str] = []
    folder = write_manifest(tmp_path, [{"id": "a", "title": "A", "licence": CC_BY}])
    monkeypatch.setattr(sys, "argv", ["seed_dspace.py", str(folder), *flags])
    monkeypatch.setenv("DSPACE_ADMIN_EMAIL", "admin@ome.test")
    monkeypatch.setenv("DSPACE_ADMIN_PASSWORD", "admin-pw")
    monkeypatch.setenv("DSPACE_COLLECTION_UUID", "col-1")
    monkeypatch.setattr(
        seed_dspace,
        "make_client",
        lambda _url: client_for(fake_dspace(existing, calls)),
    )
    monkeypatch.setattr(seed_dspace, "oai_import", lambda: calls.append("oai import"))
    seed_dspace.main()
    return calls


def test_main_creates_the_new_item_and_runs_oai_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    calls = run_main(monkeypatch, tmp_path, [])
    assert "POST /server/api/core/items" in calls
    assert calls[-1] == "oai import"
    assert capsys.readouterr().out == "a\t123456789/9\n"


def test_main_runs_oai_import_also_when_nothing_is_new(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = run_main(monkeypatch, tmp_path, ["ome-seed:a"])
    assert "POST /server/api/core/items" not in calls
    assert calls[-1] == "oai import"


def test_main_skips_oai_import_with_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = run_main(monkeypatch, tmp_path, [], "--no-oai-import")
    assert "oai import" not in calls


def test_main_stops_without_the_collection_from_the_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["seed_dspace.py"])
    monkeypatch.setenv("DSPACE_ADMIN_EMAIL", "admin@ome.test")
    monkeypatch.setenv("DSPACE_ADMIN_PASSWORD", "admin-pw")
    monkeypatch.delenv("DSPACE_COLLECTION_UUID", raising=False)
    with pytest.raises(SystemExit, match="DSPACE_COLLECTION_UUID"):
        seed_dspace.main()
