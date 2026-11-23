// Example responses of `POST /api/channel/{name}/save`, following the contract in issue #227.
// The Jest tests and the json-server middleware both read them, so they cannot drift apart.
// Replace them with the examples from the #227 pull request once that route is merged.
module.exports = {
  saved: {
    status: 200,
    body: {
      status: 'saved',
      cms_id: '148252',
      cms_url: 'https://cms.example.org/courses/148252',
    },
  },
  notFound: {
    status: 404,
    body: { detail: 'Card not found' },
  },
  alreadySaved: {
    status: 409,
    body: { detail: { cms_url: 'https://cms.example.org/courses/148252' } },
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
    body: { detail: 'CMS unreachable' },
  },
  serverError: {
    status: 500,
    body: { detail: 'Internal Server Error' },
  },
};
