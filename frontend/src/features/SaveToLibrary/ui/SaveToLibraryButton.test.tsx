import {
  act, fireEvent, render, screen, waitFor,
} from '@testing-library/react';
import axios from 'axios';
import responses from '../../../../config/mock/saveToLibraryResponses';
import { SaveToLibraryButton } from './SaveToLibraryButton';

jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

type Example = { status: number; body: unknown };

// The shape axios rejects with when the server answers with an error status.
const httpError = ({ status, body }: Example) => ({
  isAxiosError: true,
  response: { status, data: body },
});

const renderButton = () => render(
  <SaveToLibraryButton micrositeSlug="ome.eric" id={42} name="Algebra basics" />,
);
const button = () => screen.getByRole('button');
const status = () => screen.getByRole('status');
const save = () => fireEvent.click(button());

beforeEach(() => {
  mockedAxios.isAxiosError.mockImplementation((error: any) => Boolean(error?.isAxiosError));
});

describe('SaveToLibraryButton', () => {
  test('ready: shows the button and an empty status message', () => {
    renderButton();

    expect(button().textContent).toBe('Save to my library');
    expect(button().getAttribute('aria-label')).toBe('Save to my library: Algebra basics');
    expect(button().getAttribute('aria-disabled')).toBe('false');
    expect(status().textContent).toBe('');
    expect(status().getAttribute('aria-live')).toBe('polite');
    expect(mockedAxios.post).not.toHaveBeenCalled();
  });

  test('calls the save route with a relative URL, the channel in the path and the id in the body', async () => {
    mockedAxios.post.mockResolvedValue({ data: responses.saved.body });
    renderButton();

    save();

    await waitFor(() => expect(button().textContent).toBe('Saved'));
    expect(mockedAxios.post).toHaveBeenCalledTimes(1);
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/channel/ome.eric/save', { id: 42 });
    // `post` from shared/lib/req.ts would fetch a CSRF token first. FastAPI has no such route.
    expect(mockedAxios.get).not.toHaveBeenCalled();
  });

  test('sends the id as a number and escapes the channel name, as on the detail page', async () => {
    mockedAxios.post.mockResolvedValue({ data: responses.saved.body });
    render(<SaveToLibraryButton micrositeSlug="ome/eric" id="42" />);

    save();

    await waitFor(() => expect(button().textContent).toBe('Saved'));
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/channel/ome%2Feric/save', { id: 42 });
  });

  test('without a card name the visible text is the name of the button', () => {
    render(<SaveToLibraryButton micrositeSlug="ome.eric" id={42} />);

    expect(button().hasAttribute('aria-label')).toBe(false);
    expect(screen.getByRole('button', { name: 'Save to my library' })).toBe(button());
  });

  test('saved without a `cms_url` in the answer: no link', async () => {
    mockedAxios.post.mockResolvedValue({ data: { status: 'saved', cms_id: '148252' } });
    renderButton();

    save();

    await waitFor(() => expect(status().textContent).toBe('Saved to your library.'));
    expect(screen.queryByRole('link')).toBeNull();
  });

  test('saving: announces it and ignores further clicks until the answer is in', async () => {
    let answer: () => void;
    mockedAxios.post.mockReturnValue(new Promise((resolve) => {
      answer = () => resolve({ data: responses.saved.body });
    }));
    renderButton();

    save();

    expect(button().textContent).toBe('Saving…');
    expect(button().getAttribute('aria-disabled')).toBe('true');
    expect(status().textContent).toBe('Saving to your library…');

    save();
    save();
    expect(mockedAxios.post).toHaveBeenCalledTimes(1);

    await act(async () => answer());
    expect(button().textContent).toBe('Saved');
  });

  test('saved (200): announces it and links to the card in the CMS', async () => {
    mockedAxios.post.mockResolvedValue({ data: responses.saved.body });
    renderButton();

    save();

    await waitFor(() => expect(status().textContent).toBe('Saved to your library.'));
    expect(button().textContent).toBe('Saved');
    expect(button().getAttribute('aria-disabled')).toBe('true');
    expect(screen.getByRole('link').getAttribute('href')).toBe(responses.saved.body.cms_url);

    save();
    expect(mockedAxios.post).toHaveBeenCalledTimes(1);
  });

  test('already in my library (409): links to the existing card from `detail.cms_url`', async () => {
    mockedAxios.post.mockRejectedValue(httpError(responses.alreadySaved));
    renderButton();

    save();

    await waitFor(() => expect(status().textContent).toBe('This card is already in your library.'));
    expect(button().textContent).toBe('In my library');
    expect(button().getAttribute('aria-disabled')).toBe('true');
    expect(screen.getByRole('link').getAttribute('href'))
      .toBe(responses.alreadySaved.body.detail.cms_url);

    save();
    expect(mockedAxios.post).toHaveBeenCalledTimes(1);
  });

  test('not supported (501): says so and cannot be clicked again', async () => {
    mockedAxios.post.mockRejectedValue(httpError(responses.notSupported));
    renderButton();

    save();

    await waitFor(() => expect(status().textContent).toBe('Your library cannot save cards yet.'));
    expect(button().textContent).toBe('Cannot be saved');
    expect(button().getAttribute('aria-disabled')).toBe('true');
    expect(screen.queryByRole('link')).toBeNull();

    save();
    expect(mockedAxios.post).toHaveBeenCalledTimes(1);
  });

  test('card not found (404): says so and cannot be clicked again', async () => {
    mockedAxios.post.mockRejectedValue(httpError(responses.notFound));
    renderButton();

    save();

    await waitFor(() => expect(status().textContent)
      .toBe('This card no longer exists, so it cannot be saved.'));
    expect(button().textContent).toBe('Cannot be saved');
    expect(button().getAttribute('aria-disabled')).toBe('true');
    expect(screen.queryByRole('link')).toBeNull();

    save();
    expect(mockedAxios.post).toHaveBeenCalledTimes(1);
  });

  test('failed (502): offers another try, which can then succeed', async () => {
    mockedAxios.post.mockRejectedValueOnce(httpError(responses.cmsUnreachable));
    mockedAxios.post.mockResolvedValueOnce({ data: responses.saved.body });
    renderButton();

    save();

    await waitFor(() => expect(status().textContent)
      .toBe('Your library could not be reached. Nothing was saved.'));
    expect(button().textContent).toBe('Try again');
    expect(button().getAttribute('aria-disabled')).toBe('false');

    save();

    await waitFor(() => expect(button().textContent).toBe('Saved'));
    expect(mockedAxios.post).toHaveBeenCalledTimes(2);
  });

  test('failed (no answer from the server): offers another try', async () => {
    mockedAxios.post.mockRejectedValue(new Error('Network Error'));
    renderButton();

    save();

    await waitFor(() => expect(button().textContent).toBe('Try again'));
    expect(button().getAttribute('aria-disabled')).toBe('false');
  });

  test('keyboard: stays a focusable button that keeps focus in every state', async () => {
    mockedAxios.post.mockRejectedValue(httpError(responses.notSupported));
    renderButton();

    button().focus();
    expect(document.activeElement).toBe(button());
    expect(button().tagName).toBe('BUTTON');

    save();
    expect(document.activeElement).toBe(button());
    expect(button().hasAttribute('disabled')).toBe(false);

    await waitFor(() => expect(button().textContent).toBe('Cannot be saved'));
    expect(document.activeElement).toBe(button());
    expect(button().hasAttribute('disabled')).toBe(false);
  });
});
