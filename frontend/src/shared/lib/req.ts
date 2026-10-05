import axios, { AxiosError } from 'axios';
import type { Method } from 'axios';

type Payload = Record<string, unknown>;

// Relative URLs (e.g. 'groups/v1/groups') are API paths; absolute ones are used as-is.
function resolveUrl(url: string): string {
  if (url.startsWith('/') || /^https?:\/\//.test(url)) return url;
  return `/api/${url}`;
}

function getCookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

function toFormData(data: Payload): FormData {
  const formData = new FormData();
  Object.entries(data).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    if (Array.isArray(value)) {
      value.forEach((item) => formData.append(key, item instanceof Blob ? item : String(item)));
    } else if (value instanceof Blob) {
      formData.append(key, value);
    } else if (typeof value === 'object') {
      formData.append(key, JSON.stringify(value));
    } else {
      formData.append(key, String(value));
    }
  });
  return formData;
}

// Resolves with the response body. Rejects with the error response body
// (e.g. `{ detail }`) so callers can show it, or with the raw error when there is none.
async function request<T = any>(method: Method, url: string, data?: Payload, multipart = false): Promise<T> {
  const csrfToken = getCookie('csrftoken');
  try {
    const response = await axios.request<T>({
      method,
      url: resolveUrl(url),
      data: multipart && data ? toFormData(data) : data,
      headers: csrfToken ? { 'X-CSRFToken': csrfToken } : {},
    });
    return response.data;
  } catch (error) {
    throw (error as AxiosError).response?.data ?? error;
  }
}

// `multipart` sends the payload as FormData, which is needed for file uploads.
const req = {
  get: <T = any>(url: string) => request<T>('get', url),
  post: <T = any>(url: string, data?: Payload, multipart = false) => request<T>('post', url, data, multipart),
  put: <T = any>(url: string, data?: Payload, multipart = false) => request<T>('put', url, data, multipart),
  patch: <T = any>(url: string, data?: Payload, multipart = false) => request<T>('patch', url, data, multipart),
  del: <T = any>(url: string) => request<T>('delete', url),
};

export default req;
