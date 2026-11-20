"""
A small, generic OAI-PMH client (https://www.openarchives.org/OAI/2.0/).

It knows nothing about a specific repository: the base URL, identifier, set and
metadata prefix are passed in, and the plugins map the metadata. DSpace uses it
now; QUBES and OAPEN could use it later.
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx2

OAI_NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}
# OAI-PMH error codes this module reacts to.
ID_DOES_NOT_EXIST = "idDoesNotExist"
NO_RECORDS_MATCH = "noRecordsMatch"


class OAIPMHError(Exception):
    """The repository answered with an OAI-PMH <error>, e.g. idDoesNotExist."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _fetch(base_url: str, params: dict[str, str]) -> bytes:
    """Send one OAI-PMH request. Kept apart so tests can replace it."""
    return httpx2.get(base_url, params=params, timeout=30).raise_for_status().content


def _parse(content: bytes) -> ET.Element:
    """Parse an OAI-PMH answer and raise OAIPMHError when it holds an <error>."""
    # The XML comes from the repository configured for this node.
    root = ET.fromstring(content)  # noqa: S314
    if (error := root.find("oai:error", OAI_NS)) is not None:
        raise OAIPMHError(error.get("code", ""), (error.text or "").strip())
    return root


def _oai_date(value: str | datetime) -> str:
    """Format a `from` date the way OAI-PMH expects it (UTC, seconds)."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def is_deleted(record: ET.Element) -> bool:
    """A deleted record only has a header with status="deleted", no metadata."""
    header = record.find("oai:header", OAI_NS)
    return header is not None and header.get("status") == "deleted"


def get_record(base_url: str, identifier: str, metadata_prefix: str) -> ET.Element:
    """Fetch one record with verb=GetRecord and return its <record> element."""
    root = _parse(
        _fetch(
            base_url,
            {
                "verb": "GetRecord",
                "identifier": identifier,
                "metadataPrefix": metadata_prefix,
            },
        )
    )
    if (record := root.find("oai:GetRecord/oai:record", OAI_NS)) is None:
        msg = f"No record for {identifier}"
        raise OAIPMHError(ID_DOES_NOT_EXIST, msg)
    return record


def _list_page(base_url: str, params: dict[str, str]) -> ET.Element | None:
    """One page of ListRecords, or None when nothing matches (noRecordsMatch)."""
    try:
        return _parse(_fetch(base_url, params))
    except OAIPMHError as error:
        if error.code == NO_RECORDS_MATCH:
            return None
        raise


def list_records(
    base_url: str,
    metadata_prefix: str,
    *,
    set_spec: str | None = None,
    from_date: str | datetime | None = None,
    include_deleted: bool = False,
) -> Iterator[ET.Element]:
    """
    Yield every <record> of verb=ListRecords, page after page.

    set_spec limits it to one set (DSpace: ``col_<handle>``), and from_date to
    records that changed since then. Deleted records are skipped unless
    include_deleted is True. Nothing matching is not an error: it yields nothing.
    """
    params = {"verb": "ListRecords", "metadataPrefix": metadata_prefix}
    if set_spec:
        params["set"] = set_spec
    if from_date:
        params["from"] = _oai_date(from_date)

    while (root := _list_page(base_url, params)) is not None:
        for record in root.iterfind("oai:ListRecords/oai:record", OAI_NS):
            if include_deleted or not is_deleted(record):
                yield record
        token = root.findtext("oai:ListRecords/oai:resumptionToken", "", OAI_NS)
        if not token.strip():
            return
        # The next page is asked for with the token alone, as OAI-PMH requires.
        params = {"verb": "ListRecords", "resumptionToken": token.strip()}
