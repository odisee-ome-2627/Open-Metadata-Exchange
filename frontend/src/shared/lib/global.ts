export type UrlParams = Record<string, any>;

// Read the query string into a plain object. Keys that occur more than once
// (e.g. `f.std=a&f.std=b`) become arrays, which `setSearchParams` accepts back.
export function extractUrlParams(search: string = window.location.search): UrlParams {
  const params: UrlParams = {};
  new URLSearchParams(search).forEach((value, key) => {
    const current = params[key];
    if (current === undefined) {
      params[key] = value;
    } else if (Array.isArray(current)) {
      current.push(value);
    } else {
      params[key] = [current, value];
    }
  });
  return params;
}
