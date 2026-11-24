# DSpace
## Open Metadata Exchange (OME) plugin

An OME plugin defines how to import and publish metadata from an Open Education Resources system.

`plugin.py` provides the class that enables OME to understand the metadata of a [DSpace](https://dspace.lyrasis.org) repository.
It reads items through OAI-PMH with `metadataPrefix=dim`, which returns every field of an item as qualified Dublin Core (for example `dc.contributor.author`).

This plugin defines one or more:

* `mimetypes` so that DSpace json data can be stored as enclosures in InterNetNews (INN) articles.
* `newsgroups` so DSpace metadata can be published to and subscribed from those INN newsgroups.

The plugin is composed of at least three code files.

* `plugin.py`: Defines `mimetypes`, `newsgroups`, and a `make_metadata_card()` function that translates a DSpace item into an OME EducationalResource. The SPDX license comes from the license table in `oapen_books/oapen_models.py`. `make_metadata_card_from_url()` reads one item by its link (see [Which links can be published](#which-links-can-be-published)). `summarize()` writes the body of the INN article.
* `dspace_models.py`: Pydantic models for one DSpace item (the OAI-PMH header and its `dim` fields), generated with `datamodel-codegen` from `dspace_item.json` and extended with helpers to read the fields and the OAI-PMH XML.
* `save.py`: Saves a card from another library as a new item in the DSpace collection (`save_resource()` on the plugin). It maps the card to Dublin Core terms, checks on the card's `source_url` that the item is not there yet (an item whose `dc.relation` or `dc.identifier.uri` is exactly that URL), and deposits it through SWORD v2. The OME Message-ID goes in `dc.source`, so the export can skip items that came from OME. The saved item's `cms_id` is its UUID, from the edit IRI of the deposit receipt, and its `cms_url` is `<website>/items/<uuid>` (ADR 0011 §4). That also works for a collection with a review workflow, where the item only gets a handle after approval.
* `bulk_import.py`: Utilities to read a whole DSpace collection (`verb=ListRecords&metadataPrefix=dim&set=col_<handle>`, following the resumption token, with `from` to read only what changed) and convert its items into OME EducationalResources.

The OAI-PMH reader and the SWORD v2 writer are generic and live in `server/oai_pmh.py` and `server/sword.py`, so other repositories that speak OAI-PMH or SWORD v2 (EPrints, Dataverse, Hyku) can use them with their own mapping.

### Settings

The settings come from the `dspace` block in `config.json`, through `env.j2` (see `INSTALL.md`). Every setting has a default, so a node without DSpace still starts.

* `DSPACE_NEWSGROUP`, `DSPACE_SITE_NAME`, `DSPACE_LOGO`, `DSPACE_LIBRARIAN_CONTACT`: read when the plugin is imported, because `server/utils.py` reads them from the class.
* `DSPACE_SERVER_URL`: where the backend reaches DSpace, for example `http://localhost:8080/server` (`http://dspace:8080/server` inside Docker). The REST API, OAI-PMH and SWORD v2 are all under it.
* `DSPACE_OAI_URL` (optional): the OAI-PMH endpoint, only when it is not `<DSPACE_SERVER_URL>/oai/request`.
* `DSPACE_PUBLIC_SERVER_URL` (optional): DSpace's own `dspace.server.url`, for example `http://localhost:8080/server`, when it differs from `DSPACE_SERVER_URL`. SWORD only knows the collection under that address, so `save.py` sends the deposit to `DSPACE_SERVER_URL` with the host of this URL.
* `DSPACE_HANDLE_PREFIX` (optional): your DSpace's handle prefix, for example `123456789`. Only links to handles with this prefix are published, and `https://hdl.handle.net/...` links are only accepted when it is set.
* `DSPACE_COLLECTION_HANDLE`: the collection that `bulk_import.py` reads and that `save.py` deposits into, for example `123456789/2`.
* `DSPACE_DEPOSIT_EMAIL`, `DSPACE_DEPOSIT_PASSWORD`: as whom `save.py` deposits. The deposit account, never the admin.

The website address, for the item pages, is not a setting: DSpace names it as `dspaceUI` in its REST API root (see below).

### Which links can be published

A librarian publishes an item by pasting its link. The plugin accepts three kinds:

* `<website>/handle/<prefix>/<id>`, the handle page of the item;
* `<website>/items/<uuid>`, the item page of the DSpace 7+ website. The plugin asks the REST API (`<server>/api/core/items/<uuid>`) for its handle;
* `https://hdl.handle.net/<prefix>/<id>`, only when `DSPACE_HANDLE_PREFIX` is set and `<prefix>` is that prefix.

`<website>` is the address that DSpace itself gives in the REST API root (`dspaceUI` of `<server>/api`), so the plugin needs no setting for it. A link from any other website, for example another library's DSpace with the same default prefix, is refused with a 400. An item that DSpace or OAI-PMH does not know gets a 404.

The OAI identifier is `oai:<repositoryIdentifier>:<handle>`, with the `repositoryIdentifier` that `verb=Identify` announces. `dspaceUI` and the `repositoryIdentifier` are asked once and kept until the backend restarts.

### Sample data

* `dspace_item.json`: one real record from the local DSpace of the `dev` profile, converted to the JSON shape of `DSpaceItem`.
* `dspace_article.eml`: the INN article for that record, made with `make_plugin_article()` from `server/nntp_article.py`.

### Try it

```sh
uv run python -m server.plugins.dspace.plugin
DSPACE_SERVER_URL=http://localhost:8080/server DSPACE_COLLECTION_HANDLE=123456789/2 \
    uv run python -m server.plugins.dspace.bulk_import --since 2026-11-20
```

> [!NOTE]
>
> Please ***do NOT edit*** this line and below because when the docs are rebuilt, these lines will be overwritten by scripts/sync_plugin_docs.py.

**MIMETYPES:**
1. application/vnd.dspace.item+json

**NEWSGROUPS:**

{}
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
