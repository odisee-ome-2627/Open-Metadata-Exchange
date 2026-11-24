// Example responses of `POST /api/channel/{name}/save`, the same as the real route answers
// (`save_card` and `SAVE_RESPONSES` in `server/main.py`, with the DSpace plugin).
// The Jest tests and the json-server middleware both read them, so they cannot drift apart.
const UUID = '6bb452d9-d7e5-4f34-a534-630dbb6d4d31';
const SAVED_ITEM = {
  cms_id: UUID,
  cms_url: `https://repo.example.org/items/${UUID}`,
};

module.exports = {
  saved: {
    status: 200,
    body: { status: 'saved', ...SAVED_ITEM },
  },
  notFound: {
    status: 404,
    body: { detail: 'Card 7 not found in ome.dspace' },
  },
  alreadySaved: {
    status: 409,
    body: { detail: SAVED_ITEM },
  },
  cardUnreadable: {
    status: 422,
    body: { detail: 'This card cannot be read by this node' },
  },
  notSupported: {
    status: 501,
    body: { detail: 'This plugin cannot save cards' },
  },
  cmsUnreachable: {
    status: 502,
    body: { detail: 'The CMS is unreachable' },
  },
  serverError: {
    status: 500,
    body: { detail: 'Internal Server Error' },
  },
};
