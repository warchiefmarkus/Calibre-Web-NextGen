export const EXTERNAL_RATING_SOURCE_LABELS: Record<string, string> = {
  goodreads: 'Goodreads',
  hardcover: 'Hardcover',
  google_books: 'Google Books',
  open_library: 'Open Library',
};

/** Keep cover and detail scores identical without hiding meaningful decimals. */
export function formatExternalRatingScore(value: number): string {
  const fixed = value.toFixed(2);
  if (fixed.endsWith('00')) return fixed.slice(0, -1);
  if (fixed.endsWith('0')) return fixed.slice(0, -1);
  return fixed;
}
