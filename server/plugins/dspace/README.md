# DSpace
## Open Metadata Exchange (OME) plugin

An OME plugin defines how to import and publish metadata from an Open Education Resources system.

`plugin.py` provides the class that enables OME to understand the metadata of a [DSpace](https://dspace.lyrasis.org) repository.
It reads items through OAI-PMH with `metadataPrefix=dim`, which returns every field of an item as qualified Dublin Core (for example `dc.contributor.author`).

This plugin defines one or more:

* `mimetypes` so that DSpace json data can be stored as enclosures in InterNetNews (INN) articles.
* `newsgroups` so DSpace metadata can be published to and subscribed from those INN newsgroups.

The plugin is composed of at least three code files.

* `plugin.py`: Defines `mimetypes`, `newsgroups`, and a `make_metadata_card()` function that translates a DSpace item into an OME EducationalResource. The SPDX license comes from the license table in `oapen_books/oapen_models.py`. `make_metadata_card_from_url()` reads one item by its handle URL (`.../handle/<prefix>/<id>`), and `summarize()` writes the body of the INN article.
* `dspace_models.py`: Pydantic models for one DSpace item (the OAI-PMH header and its `dim` fields), generated with `datamodel-codegen` from `dspace_item.json` and extended with helpers to read the fields and the OAI-PMH XML.
* `bulk_import.py`: Utilities to read a whole DSpace collection (`verb=ListRecords&metadataPrefix=dim&set=col_<handle>`, following the resumption token, with `from` to read only what changed) and convert its items into OME EducationalResources.

The OAI-PMH reader itself is generic and lives in `server/oai_pmh.py`, so other repositories that speak OAI-PMH can use it too.

### Settings

The plugin reads its settings from the environment when it needs them, so a node without DSpace still starts.

* `DSPACE_OAI_URL`: the OAI-PMH endpoint, for example `http://localhost:8080/server/oai/request`.
* `DSPACE_COLLECTION_HANDLE`: the collection that `bulk_import.py` reads, for example `123456789/2`.

### Sample data

* `dspace_item.json`: one real record from the local DSpace of the `dev` profile, converted to the JSON shape of `DSpaceItem`.
* `dspace_article.eml`: the INN article for that record, made with `make_plugin_article()` from `server/nntp_article.py`.

### Try it

```sh
uv run python -m server.plugins.dspace.plugin
DSPACE_OAI_URL=http://localhost:8080/server/oai/request DSPACE_COLLECTION_HANDLE=123456789/2 \
    uv run python -m server.plugins.dspace.bulk_import --since 2026-11-20
```

> [!NOTE]
>
> Please ***do NOT edit*** this line and below because when the docs are rebuilt, these lines will be overwritten by scripts/sync_plugin_docs.py.

**MIMETYPES:**
1. application/vnd.dspace.item+json

**NEWSGROUPS:**

{'ome.odisee': 'Metadata from the Odisee DSpace repository'}
```text
server/plugins/dspace
├── README.md
├── __init__.py
├── bulk_import.py
├── dspace_article.eml
├── dspace_item.json
├── dspace_models.py
├── dspace_ome_item.json
└── plugin.py

1 directory, 8 files
```
