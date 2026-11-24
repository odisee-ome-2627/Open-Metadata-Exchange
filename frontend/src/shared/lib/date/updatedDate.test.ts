import { updatedDate } from './updatedDate';

describe('updatedDate', () => {
  test('shows the date the API sends in updateDate', () => {
    expect(updatedDate('2026-11-21T10:00:00Z')).toBe('Sat Nov 21 2026');
  });

  test('shows nothing instead of "Invalid Date" when the date is missing or wrong', () => {
    expect(updatedDate(undefined)).toBe('');
    expect(updatedDate(null)).toBe('');
    expect(updatedDate('not a date')).toBe('');
  });
});
