#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx2",
#     "pydantic",
# ]
# ///

"""
Publish every item of a DSpace collection to this OME node.

Reads the collection with :func:`bulk_import.read_items` and calls
``POST /api/publish_url`` once for each item that is new. It skips:

- items it published before. They are kept in a local JSON file (``--state``)
  until the node keeps this mapping itself (#228);
- items saved from OME. Saving puts the OME Message-ID in ``dc.source`` (#221),
  so these items are not our own material;
- items without ``dc.identifier.uri``. ``publish_url`` needs that handle URL.

Run ``dspace oai import`` in the DSpace container first, otherwise new items
are missing from OAI-PMH (ADR 0010).
"""

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path

import httpx2

from server.plugins.dspace.bulk_import import read_items
from server.plugins.dspace.dspace_models import DSpaceItem

# An NNTP Message-ID, e.g. <abc.123@nntp.ome.odisee.be> (RFC 5536, section 3.1.3).
MESSAGE_ID = re.compile(r"<[^<>@\s]+@[^<>@\s]+>")

DEFAULT_API_URL = "http://localhost:5001"
DEFAULT_STATE_FILE = Path("dspace_published.json")


def saved_from_ome(item: DSpaceItem) -> bool:
    """True when ``dc.source`` holds an OME Message-ID: the item came from OME."""
    return any(
        MESSAGE_ID.fullmatch(value.strip())
        for value in item.get_metadata_values("dc.source")
    )


def reason_to_skip(item: DSpaceItem, published: set[str]) -> str | None:
    """Why *item* is not published, or None when it should be."""
    if item.handle in published:
        return "already published"
    if saved_from_ome(item):
        return "saved from OME"
    if not item.get_metadata_value("dc.identifier.uri"):
        return "no dc.identifier.uri"
    return None


def load_published(path: Path) -> set[str]:
    """The handles published before, or an empty set when there is no file yet."""
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def save_published(path: Path, published: set[str]) -> None:
    """Write the handles, through a temporary file so a crash leaves no half file."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(sorted(published), indent=2), encoding="utf-8")
    temporary.replace(path)


def publish(client: httpx2.Client, api_url: str, item_url: str) -> None:
    """Ask the node to publish one item. Raises when the node refuses it."""
    response = client.post(f"{api_url}/api/publish_url", json={"url": item_url})
    response.raise_for_status()
    if response.json() is not True:
        msg = f"The node did not publish {item_url}: {response.text}"
        raise RuntimeError(msg)


def publish_items(
    items: Iterable[DSpaceItem],
    state_file: Path,
    api_url: str = DEFAULT_API_URL,
    *,
    dry_run: bool = False,
) -> int:
    """
    Publish the new items and return how many were published.

    With *dry_run*, only print them and return how many would be published.
    """
    published = load_published(state_file)
    count = 0
    with httpx2.Client(timeout=60) as client:
        for item in items:
            url = item.get_metadata_value("dc.identifier.uri")
            if reason := reason_to_skip(item, published):
                print(f"skip     {item.handle}: {reason}")
                continue
            if dry_run:
                print(f"would    {item.handle}: {url}")
                count += 1
                continue
            publish(client, api_url, url)
            published.add(item.handle)
            # Save after every item, so a second run after a crash does not post twice.
            save_published(state_file, published)
            count += 1
            print(f"publish  {item.handle}: {url}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"the OME backend, default: {DEFAULT_API_URL}",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"file with the handles published before, default: {DEFAULT_STATE_FILE}",
    )
    parser.add_argument("--oai-url", help="default: $DSPACE_OAI_URL")
    parser.add_argument(
        "--collection", help="handle, default: $DSPACE_COLLECTION_HANDLE"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="only show what would be published"
    )
    args = parser.parse_args()

    items = read_items(args.oai_url, args.collection)
    count = publish_items(items, args.state, args.api_url, dry_run=args.dry_run)
    if args.dry_run:
        print(f"{count} item(s) would be published (dry run: nothing posted)")
    else:
        print(f"{count} item(s) published")


if __name__ == "__main__":
    main()
