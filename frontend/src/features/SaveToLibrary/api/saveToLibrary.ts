import axios from 'axios';

export type SaveResult =
  | { state: 'saved'; cmsUrl?: string }
  | { state: 'already'; cmsUrl?: string }
  | { state: 'unsupported' }
  | { state: 'notFound' }
  | { state: 'unreadable' }
  | { state: 'failed' }
  | { state: 'error' };

// No answer, or the CMS behind the node is down: trying again later can work.
const RETRY_STATUSES = [502, 503, 504];

/**
 * Saves a card from another node into the librarian's own repository.
 *
 * Calls axios directly instead of `post` from `shared/lib/req.ts`: that helper first fetches
 * `/api/csrf-token/`, which the FastAPI server does not have.
 */
export async function saveToLibrary(micrositeSlug: string, id: number | string): Promise<SaveResult> {
  const cardId = Number(id);
  // `Number('abc')` is NaN, which JSON sends as null. The route would answer 422 for that.
  if (!Number.isInteger(cardId)) return { state: 'unreadable' };
  try {
    const { data } = await axios.post(
      `/api/channel/${encodeURIComponent(micrositeSlug)}/save`,
      { id: cardId },
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
    case 422:
      // The node cannot read this card. Trying again gives the same answer.
      return { state: 'unreadable' };
    default:
      if (!response || RETRY_STATUSES.includes(response.status)) return { state: 'failed' };
      return { state: 'error' };
    }
  }
}
