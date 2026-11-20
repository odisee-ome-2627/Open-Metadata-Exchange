"""
A small, generic OAI-PMH client (https://www.openarchives.org/OAI/2.0/).

It knows nothing about a specific repository: the base URL, identifier and
metadata prefix are passed in, and the plugins map the metadata. DSpace uses it
now; QUBES and OAPEN could use it later.
"""

import xml.etree.ElementTree as ET

import httpx2

OAI_NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}


class OAIPMHError(Exception):
    """The repository answered with an OAI-PMH <error>, e.g. idDoesNotExist."""


def get_record(base_url: str, identifier: str, metadata_prefix: str) -> ET.Element:
    """Fetch one record with verb=GetRecord and return its <record> element."""
    response = httpx2.get(
        base_url,
        params={
            "verb": "GetRecord",
            "identifier": identifier,
            "metadataPrefix": metadata_prefix,
        },
        timeout=30,
    ).raise_for_status()
    # The XML comes from the repository configured for this node.
    root = ET.fromstring(response.content)  # noqa: S314
    if (error := root.find("oai:error", OAI_NS)) is not None:
        msg = f"{error.get('code')}: {error.text}"
        raise OAIPMHError(msg)
    if (record := root.find(".//oai:record", OAI_NS)) is None:
        msg = f"No record for {identifier}"
        raise OAIPMHError(msg)
    return record
