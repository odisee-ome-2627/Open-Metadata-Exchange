#!/usr/bin/env -S uv run --script
#
# Reads the items of one DSpace collection through OAI-PMH:
#   GET <DSPACE_OAI_URL>?verb=ListRecords&metadataPrefix=dim&set=col_<handle>
#       [&from=<date>]
# and follows the resumption token until the last page.
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx2",
#     "pydantic",
# ]
# ///

"""
Bulk-read a DSpace collection and turn its items into OME metadata cards.

The settings come from the environment, like the rest of the plugin:

- ``DSPACE_OAI_URL``: the OAI-PMH endpoint, e.g.
  ``http://localhost:8080/server/oai/request``
- ``DSPACE_COLLECTION_HANDLE``: the collection, e.g. ``123456789/2``

Nothing runs at import time; call :func:`read_items` or :func:`bulk_import`.
"""

import argparse
import os
from collections.abc import Iterator
from datetime import datetime

from server import oai_pmh
from server.plugins.dspace.dspace_models import DSpaceItem, item_from_oai_record
from server.plugins.dspace.plugin import DSpacePlugin
from server.plugins.ome_plugin import EducationResource

METADATA_PREFIX = "dim"


def collection_set(handle: str) -> str:
    """The OAI-PMH set of a DSpace collection: 123456789/2 -> col_123456789_2."""
    return "col_" + handle.strip().replace("/", "_")


def read_items(
    oai_url: str | None = None,
    collection_handle: str | None = None,
    since: str | datetime | None = None,
) -> Iterator[DSpaceItem]:
    """
    Yield every item of the collection, or only those changed since *since*.

    Deleted items are skipped. Missing arguments are read from DSPACE_OAI_URL
    and DSPACE_COLLECTION_HANDLE.
    """
    oai_url = oai_url or os.getenv("DSPACE_OAI_URL")
    collection_handle = collection_handle or os.getenv("DSPACE_COLLECTION_HANDLE")
    if not oai_url or not collection_handle:
        msg = "Set DSPACE_OAI_URL and DSPACE_COLLECTION_HANDLE to read a collection"
        raise RuntimeError(msg)
    for record in oai_pmh.list_records(
        oai_url,
        METADATA_PREFIX,
        set_spec=collection_set(collection_handle),
        from_date=since,
    ):
        yield item_from_oai_record(record)


def bulk_import(
    oai_url: str | None = None,
    collection_handle: str | None = None,
    since: str | datetime | None = None,
) -> Iterator[EducationResource]:
    """Yield an OME metadata card for every item that :func:`read_items` finds."""
    plugin = DSpacePlugin()
    for item in read_items(oai_url, collection_handle, since):
        yield plugin.make_metadata_card(item)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--oai-url", help="default: $DSPACE_OAI_URL")
    parser.add_argument(
        "--collection", help="handle, default: $DSPACE_COLLECTION_HANDLE"
    )
    parser.add_argument(
        "--since", help="only items changed since this date, e.g. 2026-11-20"
    )
    args = parser.parse_args()

    cards = list(bulk_import(args.oai_url, args.collection, args.since))
    for card in cards:
        print(f"{card.title!r}  ({card.spdx_license_expression or 'no licence'})")
        print(f"    {card.source_url}")
    print(f"{len(cards)} item(s)")


if __name__ == "__main__":
    main()
