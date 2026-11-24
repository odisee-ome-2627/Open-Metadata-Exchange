# OME Installation

## Before you install

You will need the following:

1. At least one OME peer node that has agreed to allow you to peer with them.
1. Make sure that the peer can access your node
   1. IP address reachable from the outside by the peer.
   1. Port 119 (and 563) is unblocked for the peer.
   1. Port 119 on the host forwarded to port 1119, where Docker is listening.
   1. Only your peers can reach that port. Docker publishes it past UFW and firewalld:
      see [FIREWALL.md](FIREWALL.md).

## Peering Configuration

Copy `./config.json.example` to `./config.json` and edit it. It is not baked into the images:
Docker Compose mounts it at runtime as the `ome_config` secret, so after editing it you only need
to restart the containers, not rebuild them. The contents of the file should look as follows:

```json
{
    "organization": "ACME Org",
    "hostname": "nntp.ome.acme.org",
    "peers": [
        {
            "name": "WIDGET Org",
            "dns_name": "nntp.ome.widget.org",
            "incoming_ip": "172.21.0.1"
        }
    ],

    "nntp_user": {
        "username": "someusername",
        "password": "somepassword"
    },

    "nntp_debug_user": {
        "username": "debug_username",
        "password": "debug_password"
    },
    "local_newsgroups": [
        "ome.odisee"
    ],
    "cms_plugin": "server.plugins.qubes.plugin.QubesPlugin"
}
```

| **Variable**                  | **Value type**                  | **Comment**                                                                  |
|-------------------------------|---------------------------------|------------------------------------------------------------------------------|
| `organization`                | Name of your organization       |                                                                              |
| `hostname`                    | Publicly resolvable hostname    |                                                                              |
| `peers`                       | List of peers                   | At least one, preferably two                                                 |
| `peers[i].name`               | Org name of the peer            |                                                                              |
| `peers[i].dns_name`           | Hostname of the peer            |                                                                              |
| `peers[i].incoming_ip`        | IP address[^1]                  | The IP address of the peer you want to allow to connect.                     |
| `local_newsgroups`            | List of strings                 | Newsgroups to create locally when the NNTP server starts. For example, `["ome.odisee"]`. |
| `dspace.newsgroup`            | Newsgroup name                  | Optional. The channel of the DSpace plugin (#222). Also created when the NNTP server starts. |
| `nntp_user`                   |                                 | The username/password that the FastAPI backend uses to connect to NNTP.      |
| `nntp_user.username`          | Username                        |                                                                              |
| `nntp_user.password`          | Password                        |                                                                              |
| `nntp_debug_user`             |                                 | You can use this with an NNTP reader to access the NNTP directly.            |
| `nntp_debug_user.username`    | Debugging username              |                                                                              |
| `nntp_debug_user.password`    | Debugging password              |                                                                              |
| `cms_plugin`                  | Python classpath of CMS plugin  | E.g. `server.plugins.qubes.plugin.QubesPlugin`.                              |

[^1]: The IP address that is seen inside your Docker container might be NATted. e.g., 172.21.0.1, the gateway of the docker network.

## The node database

Every node has its own PostgreSQL (`ome-db`) for state that never goes over
NNTP: which cards were saved under which CMS ID, and later the subscribed
channels. FastAPI opens it at start and applies the SQL files in
`server/migrations/` that are new. Without `OME_DATABASE_URL` FastAPI starts
without it.

Its password is the Docker secret `ome_db_password`, a file next to
`config.json` that is never in git. Create it once:

```sh
openssl rand -base64 24 > ome_db_password
```

```sh
docker compose up -d ome-db                                   # start
docker compose exec ome-db psql -U ome -d ome                 # inspect, \dt lists the tables
docker compose down && docker compose up -d                   # the data stays
docker compose rm -sf ome-db && docker volume rm "$(docker volume ls -q | grep '_ome_pgdata$')"   # reset: empty database
```

`ome-db` publishes no port. FastAPI reaches it over the compose network, and
`psql` runs inside the container. The tests use a throwaway database of their
own, so they never touch the data of the node:

```sh
docker run -d --rm --name ome-db-test -e POSTGRES_DB=ome -e POSTGRES_USER=ome \
  -e POSTGRES_PASSWORD=ome -p 127.0.0.1:15432:5432 postgres:18
OME_DATABASE_URL=postgresql://ome:ome@127.0.0.1:15432/ome uv run pytest tests/test_db.py
docker stop ome-db-test
```

Without `OME_DATABASE_URL` those tests are skipped.

## Backend

### Plugin

## Frontend

### Integration into your platform

## DSpace test repository (development only)

A local DSpace 10.1 to test OME against (ADR 0010). It only starts with the profile `dev`.
It needs about 2 GB of free Docker memory and port 8080. Put the DSpace variables from
`.env.example` in `.env` before the first start.

```sh
docker compose --profile dev up -d     # start OME and DSpace
docker compose --profile dev --profile dspace-ui up -d   # also the DSpace UI
docker compose --profile "*" down      # stop everything, the data stays
docker compose --profile "*" down -v   # stop and delete all DSpace data
```

### Admin, collection and deposit account

Run this after every fresh start. It is safe to run again.

```sh
uv run --env-file .env scripts/dspace_bootstrap.py
```

It creates the admin, the collection `Course material` in the community `OME test`, and the
deposit account that OME saves with. Put the two lines it prints in `.env`.

### Test items

Fill the collection after the bootstrap. It is safe to run again: items that are already
there are skipped.

```sh
uv run --env-file .env scripts/seed_dspace.py              # the open set in seed/open
uv run --env-file .env scripts/seed_dspace.py seed/local   # course material, never in git
```

`seed/open/manifest.json` shows the format. Only `id`, `title` and `licence` are required, and `id`
must never change. A `file` (PDF, EPUB, dataset) is uploaded with the item, a `url` is only a link.
Put course material and its files in `seed/local/`, which is in
`.gitignore`. The script runs `dspace oai import` at the end, so OME sees the new items.

### URLs

On a server, set `DSPACE_HOST` in `.env` to its public address before the bootstrap and the
seed. Every item stores the link to its page, and other nodes open it, so items made with
`localhost` keep that link. Use `localhost` below on your own machine.

- REST API: `http://localhost:8080/server/api`
- OAI-PMH, where OME reads: `http://localhost:8080/server/oai/request?verb=Identify`
- SWORD v2, where OME saves: `http://localhost:8080/server/swordv2/servicedocument`
- DSpace UI, only with the profile `dspace-ui`: `http://localhost:4001`

New items only show up in OAI-PMH after
`docker compose --profile dev exec dspace /dspace/bin/dspace oai import`.

### The DSpace plugin

To publish items from this DSpace on your node, set the plugin in your local `config.json`:

```json
"cms_plugin": "server.plugins.dspace.plugin.DSpacePlugin"
```

The plugin reads DSpace through OAI-PMH. Add a `dspace` block to the same `config.json`:

```json
"dspace": {
    "oai_url": "http://dspace:8080/server/oai/request",
    "handle_prefix": "123456789",
    "collection_handle": "123456789/2",
    "newsgroup": "ome.example",
    "site_name": "ExampleLibrary",
    "logo": "https://example.org/logo.png",
    "librarian_contact": "librarian@example.org"
}
```

`env.j2` turns it into `DSPACE_OAI_URL`, `DSPACE_HANDLE_PREFIX`, `DSPACE_COLLECTION_HANDLE`,
`DSPACE_NEWSGROUP`, `DSPACE_SITE_NAME`, `DSPACE_LOGO` and `DSPACE_LIBRARIAN_CONTACT` for the
backend. `newsgroup`, `site_name`, `logo` and `librarian_contact` are your library's own:
the values above are placeholders. `handle_prefix` is your DSpace's handle prefix; it is
optional and only needed to publish `hdl.handle.net` links.
`newsgroup` is required on a node whose `cms_plugin` is the DSpace plugin: the Explore page
takes the first newsgroup of the node's own plugin. The news server creates this group at
start-up, so fill in the name only here. Do not add it to `local_newsgroups` as well: that
list is for the channels of other libraries. `site_name`, `logo` and `librarian_contact` are
optional; without them the defaults of `OMEPlugin` apply. Inside
Docker the backend reaches DSpace at `dspace:8080`, not `localhost`. Use the handle that the
bootstrap printed. The settings are read when they are needed, so a node without a `dspace`
block still starts. See `server/plugins/dspace/README.md`.
