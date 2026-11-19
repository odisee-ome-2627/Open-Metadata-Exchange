#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx2",
# ]
# ///

"""
Fill the DSpace collection of the Compose profile `dev` from a manifest.

Reads `manifest.json` in a folder and creates every item that is not in the
collection yet. Safe to run again: each item carries its seed ID in
`dc.identifier.other`. Then runs `dspace oai import`, because OAI-PMH only
shows new items after that.

    uv run --env-file .env scripts/seed_dspace.py              # open set, in git
    uv run --env-file .env scripts/seed_dspace.py seed/local   # course material
"""

import argparse
import json
import mimetypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx2
from dspace_bootstrap import (
    DEFAULT_URL,
    REPO_ROOT,
    json_of,
    login,
    make_client,
    wait_for_dspace,
)

SEED_PREFIX = "ome-seed:"
OPEN_SET = REPO_ROOT / "seed" / "open"
REQUIRED_FIELDS = ("id", "title", "licence")
TEXT_FIELDS = (
    *REQUIRED_FIELDS,
    "description",
    "date",
    "type",
    "language",
    "url",
    "file",
)
LIST_FIELDS = ("authors", "subjects")


@dataclass(frozen=True)
class SeedItem:
    id: str
    title: str
    licence: str
    description: str = ""
    authors: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    date: str = ""
    type: str = "Learning Object"
    language: str = "en"
    url: str = ""
    file: str = ""


def to_seed_item(raw: object, where: str) -> SeedItem:
    """Check one item of the manifest, so a typo stops the script before DSpace."""
    if not isinstance(raw, dict):
        msg = f"{where} must be an object with fields"
        raise SystemExit(msg)
    if unknown := raw.keys() - {*TEXT_FIELDS, *LIST_FIELDS}:
        msg = f"{where} has unknown fields: {', '.join(sorted(unknown))}"
        raise SystemExit(msg)
    # No default licence: material without one must not be published as open
    if missing := [name for name in REQUIRED_FIELDS if not raw.get(name)]:
        msg = f"{where} needs {', '.join(missing)}"
        raise SystemExit(msg)
    for name in TEXT_FIELDS:
        if name in raw and not isinstance(raw[name], str):
            msg = f"{where}: {name} must be text"
            raise SystemExit(msg)
    for name in LIST_FIELDS:
        values = raw.get(name, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            msg = f"{where}: {name} must be a list of texts"
            raise SystemExit(msg)
    lists = {name: tuple(raw.get(name, ())) for name in LIST_FIELDS}
    return SeedItem(**{**raw, **lists})


def read_manifest(folder: Path) -> list[SeedItem]:
    """Read and check `manifest.json`, without talking to DSpace."""
    manifest = folder / "manifest.json"
    raw_items = json.loads(manifest.read_text(encoding="utf-8")).get("items")
    if not isinstance(raw_items, list):
        msg = f"{manifest} needs a list under the key items"
        raise SystemExit(msg)
    items: list[SeedItem] = []
    seen: set[str] = set()
    for number, raw in enumerate(raw_items, start=1):
        item = to_seed_item(raw, f"Item {number} in {manifest}")
        if item.id in seen:
            msg = f"The seed ID {item.id} appears twice in {manifest}"
            raise SystemExit(msg)
        seen.add(item.id)
        if item.file and not (folder / item.file).is_file():
            msg = f"{item.id}: {folder / item.file} does not exist"
            raise SystemExit(msg)
        items.append(item)
    return items


def to_metadata(item: SeedItem) -> dict[str, list[dict[str, str]]]:
    """The DSpace metadata of an item, without empty fields."""
    fields = {
        "dc.title": [item.title],
        "dc.identifier.other": [SEED_PREFIX + item.id],
        "dc.description.abstract": [item.description],
        "dc.contributor.author": list(item.authors),
        "dc.subject": list(item.subjects),
        "dc.rights.uri": [item.licence],
        "dc.date.issued": [item.date],
        "dc.type": [item.type],
        "dc.language.iso": [item.language],
        # Not dc.source: saved OME items carry their Message-ID there (ADR 0011)
        "dc.relation.uri": [item.url],
    }
    return {
        key: [{"value": value} for value in values if value]
        for key, values in fields.items()
        if any(values)
    }


def existing_seed_ids(client: httpx2.Client, collection_uuid: str) -> set[str]:
    """The seed IDs of every item in the collection, page by page."""
    found: set[str] = set()
    page = 0
    while True:
        response = client.get(
            "discover/search/objects",
            params={
                "scope": collection_uuid,
                "dsoType": "ITEM",
                "size": 100,
                "page": page,
            },
        )
        result = json_of(response)["_embedded"]["searchResult"]
        for found_object in result.get("_embedded", {}).get("objects", []):
            metadata = found_object["_embedded"]["indexableObject"]["metadata"]
            for value in metadata.get("dc.identifier.other", []):
                if value["value"].startswith(SEED_PREFIX):
                    found.add(value["value"].removeprefix(SEED_PREFIX))
        page += 1
        if page >= result["page"]["totalPages"]:
            return found


def create_item(client: httpx2.Client, collection_uuid: str, item: SeedItem) -> dict:
    return json_of(
        client.post(
            "core/items",
            params={"owningCollection": collection_uuid},
            json={
                "name": item.title,
                "inArchive": True,
                "discoverable": True,
                "withdrawn": False,
                "type": "item",
                "metadata": to_metadata(item),
            },
        )
    )


def upload_file(client: httpx2.Client, item_uuid: str, path: Path) -> None:
    """Put the file in the bundle ORIGINAL, where DSpace keeps the files of an item.

    Any format works: DSpace recognises PDF, EPUB, CSV, ZIP and others itself.
    """
    bundle = json_of(
        client.post(f"core/items/{item_uuid}/bundles", json={"name": "ORIGINAL"})
    )
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as file:
        client.post(
            f"core/bundles/{bundle['uuid']}/bitstreams",
            files={"file": (path.name, file, content_type)},
        ).raise_for_status()


def seed(
    client: httpx2.Client, collection_uuid: str, items: list[SeedItem], folder: Path
) -> list[tuple[SeedItem, dict]]:
    """Create the items that are not in the collection yet, with their file."""
    existing = existing_seed_ids(client, collection_uuid)
    created = []
    for item in items:
        if item.id in existing:
            continue
        dspace_item = create_item(client, collection_uuid, item)
        if item.file:
            try:
                upload_file(client, dspace_item["uuid"], folder / item.file)
            except Exception:
                # A next run skips an item that exists, so it would never get its file
                client.delete(f"core/items/{dspace_item['uuid']}")
                raise
        created.append((item, dspace_item))
    return created


def oai_import() -> None:
    """OAI-PMH only shows new items after this."""
    subprocess.run(
        [  # noqa: S607
            "docker",
            "compose",
            "--profile",
            "dev",
            "exec",
            "-T",
            "dspace",
            "/dspace/bin/dspace",
            "oai",
            "import",
        ],
        check=True,
        cwd=REPO_ROOT,  # docker compose looks for docker-compose.yml here
        stdout=sys.stderr,  # keep stdout for the list of new items
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill the DSpace test collection.")
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=OPEN_SET,
        help="folder with manifest.json (default: seed/open)",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("DSPACE_URL", DEFAULT_URL),
        help="REST server of DSpace. `oai import` always runs in the container of"
        " this compose project, so add --no-oai-import for another DSpace.",
    )
    parser.add_argument(
        "--no-oai-import",
        action="store_true",
        help="skip `dspace oai import`, for a DSpace outside this project",
    )
    args = parser.parse_args()
    env = {
        name: os.getenv(name, "")
        for name in (
            "DSPACE_ADMIN_EMAIL",
            "DSPACE_ADMIN_PASSWORD",
            "DSPACE_COLLECTION_UUID",
        )
    }
    if missing := [name for name, value in env.items() if not value]:
        msg = f"Set {', '.join(missing)} in .env, after scripts/dspace_bootstrap.py"
        raise SystemExit(msg)
    items = read_manifest(args.folder)
    with make_client(args.url) as client:
        wait_for_dspace(client)
        if not login(client, env["DSPACE_ADMIN_EMAIL"], env["DSPACE_ADMIN_PASSWORD"]):
            msg = "Cannot log in as the DSpace admin. Check DSPACE_ADMIN_PASSWORD"
            raise SystemExit(msg)
        created = seed(client, env["DSPACE_COLLECTION_UUID"], items, args.folder)
    for item, dspace_item in created:
        print(f"{item.id}\t{dspace_item['handle']}")
    skipped = len(items) - len(created)
    print(f"{len(created)} created, {skipped} already there", file=sys.stderr)
    # Also when nothing is new: a run that stopped halfway created items but never
    # imported them. The import only adds what OAI-PMH does not have yet.
    if not args.no_oai_import:
        oai_import()


if __name__ == "__main__":
    main()
