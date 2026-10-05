import axios from 'axios';

// Value of the "Select ..." placeholder option.
export const BLANK_VALUE = '';

const ALIGN_WIDGET_HELPER_URL = '/standards/align-widget-helper';

// Fetch the next step of the alignment cascade (standard -> area -> tags), the same
// endpoint `AlignWidget` uses. Without arguments it returns the list of standards.
// Responds with `{ name, label, choices }`; for tags `choices` is `{ options }`.
export function fetchStandards(field?: string, value?: string, standard?: string | null) {
  const params: Record<string, string> = {};
  if (field) {
    params.field = field;
    params[field] = value ?? BLANK_VALUE;
  }
  if (standard && field !== 'standard') params.standard = standard;
  return axios.get(ALIGN_WIDGET_HELPER_URL, { params });
}
