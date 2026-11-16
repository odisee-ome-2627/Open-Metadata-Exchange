import { useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Link from '@mui/material/Link';
import Typography from '@mui/material/Typography';
import { saveToLibrary, SaveResult } from '../api/saveToLibrary';

type State = 'ready' | 'saving' | SaveResult['state'];

interface SaveToLibraryButtonProps {
  micrositeSlug: string;
  id: number | string;
  /** Name of the card, so a screen reader user knows which card a button belongs to. */
  name?: string;
  size?: 'small' | 'medium' | 'large';
}

// Texts are provisional until the wireframe of issue #233 is agreed.
const LABELS: Record<State, string> = {
  ready: 'Save to my library',
  saving: 'Saving…',
  saved: 'Saved',
  already: 'In my library',
  unsupported: 'Cannot be saved',
  notFound: 'Cannot be saved',
  failed: 'Try again',
};

const MESSAGES: Record<State, string> = {
  ready: '',
  saving: 'Saving to your library…',
  saved: 'Saved to your library.',
  already: 'This card is already in your library.',
  unsupported: 'Your library cannot save cards yet.',
  notFound: 'This card no longer exists, so it cannot be saved.',
  failed: 'Your library could not be reached. Nothing was saved.',
};

const ERROR_STATES: State[] = ['unsupported', 'notFound', 'failed'];
// Only these states do something on click. The others keep the button focusable but inert.
const CLICKABLE_STATES: State[] = ['ready', 'failed'];

export function SaveToLibraryButton({
  micrositeSlug, id, name, size = 'medium',
}: SaveToLibraryButtonProps) {
  const [state, setState] = useState<State>('ready');
  const [cmsUrl, setCmsUrl] = useState<string>();

  const clickable = CLICKABLE_STATES.includes(state);

  const handleClick = async () => {
    if (!clickable) return;
    setState('saving');
    const result = await saveToLibrary(micrositeSlug, id);
    setCmsUrl('cmsUrl' in result ? result.cmsUrl : undefined);
    setState(result.state);
  };

  return (
    <Box sx={{
      display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1,
    }}
    >
      {/* `aria-disabled` instead of `disabled`: a disabled button drops keyboard focus, and
          the user would lose their place in the page right when the result comes in. */}
      <Button
        type="button"
        size={size}
        variant={state === 'ready' || state === 'failed' ? 'contained' : 'outlined'}
        color={state === 'saved' || state === 'already' ? 'success' : 'primary'}
        aria-disabled={!clickable}
        aria-label={name ? `${LABELS[state]}: ${name}` : undefined}
        onClick={handleClick}
        sx={clickable ? undefined : { cursor: 'default' }}
      >
        {LABELS[state]}
      </Button>
      {/* Always in the DOM, so screen readers announce the text when it changes. */}
      <Typography
        component="span"
        variant="body2"
        role="status"
        aria-live="polite"
        color={ERROR_STATES.includes(state) ? 'error' : 'text.secondary'}
      >
        {MESSAGES[state]}
      </Typography>
      {cmsUrl && (state === 'saved' || state === 'already') && (
        <Link
          href={cmsUrl}
          variant="body2"
          aria-label={name ? `Open in my library: ${name}` : undefined}
        >
          Open in my library
        </Link>
      )}
    </Box>
  );
}
