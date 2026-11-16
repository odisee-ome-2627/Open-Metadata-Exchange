import axios from 'axios';

export type SaveResult =
  | { state: 'saved'; cmsUrl?: string }
  | { state: 'already'; cmsUrl?: string }
  | { state: 'unsupported' }
  | { state: 'notFound' }
  | { state: 'failed' };

/**
 * Saves a card from another node into the librarian's own repository.
 *
 * Calls axios directly instead of `post` from `shared/lib/req.ts`: that helper first fetches
 * `/api/csrf-token/`, which the FastAPI server does not have.
 */
export async function saveToLibrary(micrositeSlug: string, id: number | string): Promise<SaveResult> {
  try {
    const { data } = await axios.post(
      `/api/channel/${encodeURIComponent(micrositeSlug)}/save`,
      { id: Number(id) },
    );
    return { state: 'saved', cmsUrl: data?.cms_url };
  } catch (error) {
    const response = axios.isAxiosError(error) ? error.response : undefined;
    switch (response?.status) {
    case 409:
      return { state: 'already', cmsUrl: (response.data as any)?.detail?.cms_url };
    case 501:
      return { state: 'unsupported' };
    case 404:
      return { state: 'notFound' };
    default:
      // 502 (CMS unreachable), no response at all, or anything unexpected: worth another try.
      return { state: 'failed' };
    }
  }
}
