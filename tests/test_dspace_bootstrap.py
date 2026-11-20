"""Tests for `scripts/dspace_bootstrap.py` against a fake DSpace."""

import importlib.util
from pathlib import Path

import httpx2
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dspace_bootstrap.py"
_spec = importlib.util.spec_from_file_location("dspace_bootstrap", SCRIPT)
assert _spec
assert _spec.loader
dspace_bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dspace_bootstrap)

BASE = "http://dspace.test/server"
SETTINGS = dspace_bootstrap.Settings(
    admin_email="admin@ome.test",
    admin_password="admin-pw",  # noqa: S106
    deposit_email="deposit@ome.test",
    deposit_password="deposit-pw",  # noqa: S106
    community="OME test",
    collection="Course material",
)
COMMUNITY = {"uuid": "com-1", "name": "OME test"}
COLLECTION = {"uuid": "col-1", "name": "Course material", "handle": "123456789/2"}
DEPOSIT = {
    "id": "eperson-1",
    "_links": {"self": {"href": f"{BASE}/api/eperson/epersons/eperson-1"}},
}
GROUP = {"id": "group-1"}

LISTS = {
    "core/communities/search/top": ("communities", COMMUNITY),
    "core/communities/com-1/collections": ("collections", COLLECTION),
    "eperson/groups/group-1/epersons": ("epersons", DEPOSIT),
}
SINGLES = {
    "eperson/epersons/search/byEmail": DEPOSIT,
    "core/collections/col-1/submittersGroup": GROUP,
}
CREATED = {
    "core/communities": (201, COMMUNITY),
    "core/collections": (201, COLLECTION),
    "eperson/epersons": (201, DEPOSIT),
    "core/collections/col-1/submittersGroup": (201, GROUP),
    "eperson/groups/group-1/epersons": (204, None),
}


def page(key: str, items: list[dict]) -> dict:
    return {"_embedded": {key: items}}


class FakeDSpace:
    """Answers like DSpace 10.1. Empty, or with everything already there."""

    def __init__(self, *, complete: bool) -> None:
        self.complete = complete
        self.admin_exists = complete
        self.failed_logins = 0
        self.posts: list[str] = []
        self.csrf_on_posts: list[str | None] = []
        self.member_posts: list[tuple[str | None, str]] = []

    def create_admin(self, email: str, password: str) -> None:
        assert (email, password) == ("admin@ome.test", "admin-pw")
        self.admin_exists = True

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.removeprefix("/server/api/")
        public = path in {"security/csrf", "authn/login"}
        if not public and request.headers.get("Authorization") != "Bearer jwt":
            return httpx2.Response(401)  # like DSpace without a login
        if path == "eperson/groups/group-1/epersons" and request.method == "POST":
            content_type = request.headers.get("Content-Type")
            self.member_posts.append((content_type, request.content.decode()))
        if request.method == "POST":
            self.csrf_on_posts.append(request.headers.get("X-XSRF-TOKEN"))
            if path != "authn/login":
                self.posts.append(path)
        return self.answer(request.method, path)

    def login(self) -> httpx2.Response:
        if not self.admin_exists:
            self.failed_logins += 1
            return httpx2.Response(401)
        headers = {"Authorization": "Bearer jwt", "DSPACE-XSRF-TOKEN": "token-2"}
        return httpx2.Response(200, headers=headers)

    def answer(self, method: str, path: str) -> httpx2.Response:
        if path == "security/csrf":
            return httpx2.Response(204, headers={"DSPACE-XSRF-TOKEN": "token-1"})
        if path == "authn/login":
            return self.login()
        if method == "POST":
            status, body = CREATED[path]
            return httpx2.Response(status, json=body)
        if path in LISTS:
            key, item = LISTS[path]
            return httpx2.Response(200, json=page(key, [item] if self.complete else []))
        if self.complete:
            return httpx2.Response(200, json=SINGLES[path])
        return httpx2.Response(204)


def test_the_admin_password_is_not_in_the_docker_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(
        dspace_bootstrap.subprocess,
        "run",
        lambda command, **options: runs.append((command, options)),
    )
    email, password = SETTINGS.admin_email, SETTINGS.admin_password
    dspace_bootstrap.create_admin_in_container(email, password)
    [(command, options)] = runs
    assert not any(password in part for part in command)
    assert options["env"]["OME_ADMIN_PASSWORD"] == password
    assert email in command


def run(fake: FakeDSpace) -> dict:
    transport = httpx2.MockTransport(fake.handle)
    with dspace_bootstrap.make_client(BASE, transport) as client:
        return dspace_bootstrap.bootstrap(client, SETTINGS, fake.create_admin)


def test_empty_dspace_gets_admin_collection_and_deposit_account(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeDSpace(complete=False)
    assert run(fake) == COLLECTION
    assert "already exists" not in capsys.readouterr().err
    # DSpace caches a failed login, so the admin must exist before the first one
    assert fake.failed_logins == 0
    assert fake.posts == [
        "core/communities",
        "core/collections",
        "eperson/epersons",
        "core/collections/col-1/submittersGroup",
        "eperson/groups/group-1/epersons",
    ]
    assert fake.member_posts == [("text/uri-list", DEPOSIT["_links"]["self"]["href"])]


def test_second_run_changes_nothing() -> None:
    fake = FakeDSpace(complete=True)
    assert run(fake) == COLLECTION
    assert fake.posts == []


def test_an_existing_deposit_account_keeps_its_password_with_a_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run(FakeDSpace(complete=True))
    captured = capsys.readouterr()
    assert "DSPACE_DEPOSIT_PASSWORD is not applied" in captured.err
    assert captured.out == ""  # stdout only holds the lines for .env


def test_an_error_while_looking_up_the_account_stops_the_script() -> None:
    fake = FakeDSpace(complete=False)
    transport = httpx2.MockTransport(
        lambda request: (
            httpx2.Response(500)
            if request.url.path.endswith("search/byEmail")
            else fake.handle(request)
        )
    )
    with (
        dspace_bootstrap.make_client(BASE, transport) as client,
        pytest.raises(httpx2.HTTPStatusError),
    ):
        dspace_bootstrap.bootstrap(client, SETTINGS, fake.create_admin)
    assert "eperson/epersons" not in fake.posts


@pytest.mark.parametrize(
    "base", [BASE, f"{BASE}/", f"{BASE}/api", f"{BASE}/api/"], ids=str
)
def test_the_url_may_end_in_a_slash_or_api(base: str) -> None:
    fake = FakeDSpace(complete=True)
    transport = httpx2.MockTransport(fake.handle)
    with dspace_bootstrap.make_client(base, transport) as client:
        assert dspace_bootstrap.bootstrap(client, SETTINGS, fake.create_admin)


def test_every_post_sends_the_latest_csrf_token() -> None:
    fake = FakeDSpace(complete=False)
    run(fake)
    assert fake.csrf_on_posts == ["token-1"] + ["token-2"] * 5
