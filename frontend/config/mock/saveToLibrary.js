// json-server middleware that answers `POST /api/channel/{name}/save` with the example
// responses, so every state of the save button can be tried in the browser.
//
//   npm run start:dev:server                              (json-server on port 3000)
//   REACT_APP_SAVE_API_URL=http://localhost:3000 npm start
//
// The last digit of the card id picks the answer:
//   1 -> 404 not found, 2 -> 409 already saved, 3 -> 501 not supported,
//   4 -> 502 CMS unreachable, anything else -> 200 saved.
// Set SAVE_MOCK to saved, notFound, alreadySaved, notSupported or cmsUnreachable to force
// one answer for every card, and SAVE_MOCK_DELAY (ms) to change how long "Saving" shows.
const responses = require('./saveToLibraryResponses');

const SAVE_ROUTE = /^\/api\/channel\/[^/]+\/save\/?$/;
const BY_LAST_DIGIT = {
  1: 'notFound', 2: 'alreadySaved', 3: 'notSupported', 4: 'cmsUnreachable',
};
const DELAY = Number(process.env.SAVE_MOCK_DELAY || 1000);

module.exports = (req, res, next) => {
  if (req.method !== 'POST' || !SAVE_ROUTE.test(req.path)) {
    next();
    return;
  }
  const id = Number(req.body && req.body.id);
  const name = process.env.SAVE_MOCK || BY_LAST_DIGIT[Math.abs(id) % 10] || 'saved';
  const response = responses[name] || responses.saved;
  setTimeout(() => res.status(response.status).json(response.body), DELAY);
};
