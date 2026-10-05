#!/bin/bash

# Create the newsgroups that the OME plugins use on the local INN server.
# Without them the API returns 500 errors like `411 No such group ome.collage_photos`.
#
# Run from the repository root after `docker compose up`:
#   scripts/create_local_newsgroups.sh
# The INN container has no volume, so run it again after `docker compose down`.

set -euo pipefail

docker compose exec -T fastapi-server uv run --env-file=.env python -c \
  "from server.get_ome_plugins import get_newsgroups_from_plugins; print('\n'.join(get_newsgroups_from_plugins()))" |
  while read -r newsgroup; do
    printf '%s: ' "${newsgroup}"
    # </dev/null keeps `docker compose exec` from reading the rest of the newsgroup list.
    docker compose exec -T nntp-server su news -s /bin/sh -c "/usr/lib/news/bin/ctlinnd newgroup ${newsgroup}" </dev/null
  done
