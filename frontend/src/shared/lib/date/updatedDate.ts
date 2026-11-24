// The "Updated" date on a resource card, or nothing when the API sent no valid date,
// so a card never shows "Invalid Date" (#239).
export function updatedDate(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toDateString();
}
