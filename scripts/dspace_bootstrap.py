#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx2",
# ]
# ///

"""
Set up the DSpace test repository of the Compose profile `dev` (#215).

Creates the admin, one community, one collection in it, and an OME deposit
account that may only submit to that collection. Safe to run again: it looks up
what exists before it creates anything. Prints the collection UUID and handle
for `.env`. An existing deposit account keeps its password.

    uv run --env-file .env scripts/dspace_bootstrap.py
"""

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx2

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "http://localhost:8080/server"
CSRF_HEADER = "DSPACE-XSRF-TOKEN"
PAGE_SIZE = {"size": 1000}


@dataclass(frozen=True)
class Settings:
    admin_email: str
    admin_password: str
    deposit_email: str
    deposit_password: str
    community: str
    collection: str


def make_client(
    base_url: str, transport: httpx2.BaseTransport | None = None
) -> httpx2.Client:
    """A client that keeps the CSRF token of DSpace up to date."""
    # Accept .../server, .../server/ and .../server/api alike
    server = base_url.rstrip("/").removesuffix("/api")
    client = httpx2.Client(base_url=f"{server}/api/", transport=transport, timeout=30)

    def remember_csrf_token(response: httpx2.Response) -> None:
        if token := response.headers.get(CSRF_HEADER):
            client.headers["X-XSRF-TOKEN"] = token

    client.event_hooks["response"] = [remember_csrf_token]
    return client


def wait_for_dspace(client: httpx2.Client, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    waiting = False
    while True:
        try:
            response = client.get("security/csrf")
            if response.is_success:
                return
            if response.is_client_error:  # a wrong URL, waiting will not help
                msg = f"DSpace answers {response.status_code} at {response.url}"
                raise SystemExit(msg)
        except httpx2.TransportError:
            pass
        if time.monotonic() > deadline:
            msg = f"DSpace does not answer at {client.base_url}"
            raise SystemExit(msg)
        if not waiting:
            print(f"Waiting for DSpace at {client.base_url} ...", file=sys.stderr)
            waiting = True
        time.sleep(5)


def login(client: httpx2.Client, email: str, password: str) -> bool:
    client.get("security/csrf")
    response = client.post("authn/login", data={"user": email, "password": password})
    if response.status_code == httpx2.codes.UNAUTHORIZED:
        return False
    response.raise_for_status()
    client.headers["Authorization"] = response.headers["Authorization"]
    return True


def create_admin_in_container(email: str, password: str) -> None:
    """The first admin can only be made inside the backend container."""
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "compose",
            "--profile",
            "dev",
            "exec",
            "-T",
            "dspace",
            "/dspace/bin/dspace",
            "create-administrator",
            "-e",
            email,
            "-f",
            "Admin",
            "-l",
            "OME",
            "-p",
            password,
            "-c",
            "en",
        ],
        check=True,
        cwd=REPO_ROOT,  # docker compose looks for docker-compose.yml here
        stdout=sys.stderr,  # keep stdout for the two lines that go into .env
    )


def embedded(response: httpx2.Response, key: str) -> list[dict]:
    response.raise_for_status()
    return response.json().get("_embedded", {}).get(key, [])


def named(name: str) -> dict:
    return {"name": name, "metadata": {"dc.title": [{"value": name}]}}


def json_of(response: httpx2.Response) -> dict:
    response.raise_for_status()
    return response.json()


def ensure_community(client: httpx2.Client, name: str) -> dict:
    response = client.get("core/communities/search/top", params=PAGE_SIZE)
    for community in embedded(response, "communities"):
        if community["name"] == name:
            return community
    return json_of(client.post("core/communities", json=named(name)))


def ensure_collection(client: httpx2.Client, community_uuid: str, name: str) -> dict:
    response = client.get(
        f"core/communities/{community_uuid}/collections", params=PAGE_SIZE
    )
    for collection in embedded(response, "collections"):
        if collection["name"] == name:
            return collection
    return json_of(
        client.post(
            "core/collections", params={"parent": community_uuid}, json=named(name)
        )
    )


def ensure_eperson(client: httpx2.Client, email: str, password: str) -> dict:
    response = client.get("eperson/epersons/search/byEmail", params={"email": email})
    if response.status_code == httpx2.codes.OK:
        return response.json()
    if response.status_code != httpx2.codes.NO_CONTENT:  # 204 means: no such account
        response.raise_for_status()
    eperson = {
        "email": email,
        "canLogIn": True,
        "password": password,
        "metadata": {
            "eperson.firstname": [{"value": "OME"}],
            "eperson.lastname": [{"value": "Deposit"}],
        },
    }
    return json_of(client.post("eperson/epersons", json=eperson))


def ensure_submitter(
    client: httpx2.Client, collection_uuid: str, eperson: dict
) -> None:
    """Put the account in the submitters group of this one collection."""
    url = f"core/collections/{collection_uuid}/submittersGroup"
    response = client.get(url)
    if response.status_code == httpx2.codes.NO_CONTENT:
        response = client.post(url, json={})
    group = json_of(response)
    members_url = f"eperson/groups/{group['id']}/epersons"
    members = embedded(client.get(members_url, params=PAGE_SIZE), "epersons")
    if any(member["id"] == eperson["id"] for member in members):
        return
    client.post(
        members_url,
        content=eperson["_links"]["self"]["href"],
        headers={"Content-Type": "text/uri-list"},
    ).raise_for_status()


def bootstrap(
    client: httpx2.Client,
    settings: Settings,
    create_admin: Callable[[str, str], None] = create_admin_in_container,
) -> dict:
    """Return the collection, after making sure everything exists."""
    wait_for_dspace(client)
    # Create the admin before the first login, even when it exists. DSpace
    # caches a failed login, so an admin made after one cannot log in until
    # the backend restarts. Running create-administrator again is harmless.
    create_admin(settings.admin_email, settings.admin_password)
    if not login(client, settings.admin_email, settings.admin_password):
        msg = (
            "Cannot log in as the DSpace admin. Did DSPACE_ADMIN_PASSWORD change?"
            " Then run: docker compose --profile dev restart dspace"
        )
        raise SystemExit(msg)
    community = ensure_community(client, settings.community)
    collection = ensure_collection(client, community["uuid"], settings.collection)
    deposit = ensure_eperson(client, settings.deposit_email, settings.deposit_password)
    ensure_submitter(client, collection["uuid"], deposit)
    return collection


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up the DSpace test repository of the profile dev."
    )
    parser.add_argument("--community", default="OME test")
    parser.add_argument("--collection", default="Course material")
    parser.add_argument(
        "--url",
        default=os.getenv("DSPACE_URL", DEFAULT_URL),
        help="REST server of the DSpace in this compose project. The admin is"
        " always created in its container, so do not point this elsewhere.",
    )
    args = parser.parse_args()
    env = {
        name: os.getenv(name, "")
        for name in (
            "DSPACE_ADMIN_EMAIL",
            "DSPACE_ADMIN_PASSWORD",
            "DSPACE_DEPOSIT_EMAIL",
            "DSPACE_DEPOSIT_PASSWORD",
        )
    }
    if missing := [name for name, value in env.items() if not value]:
        msg = f"Set {', '.join(missing)} in .env"
        raise SystemExit(msg)
    settings = Settings(
        admin_email=env["DSPACE_ADMIN_EMAIL"],
        admin_password=env["DSPACE_ADMIN_PASSWORD"],
        deposit_email=env["DSPACE_DEPOSIT_EMAIL"],
        deposit_password=env["DSPACE_DEPOSIT_PASSWORD"],
        community=args.community,
        collection=args.collection,
    )
    with make_client(args.url) as client:
        collection = bootstrap(client, settings)
    print(f"DSPACE_COLLECTION_UUID={collection['uuid']}")
    print(f"DSPACE_COLLECTION_HANDLE={collection['handle']}")


if __name__ == "__main__":
    main()
