export const FOLIATE_READER_FORMATS = new Set([
  'epub', 'kepub', 'fb2', 'fbz', 'mobi', 'azw', 'azw3', 'cbz',
]);

const NATIVE_READER_FORMATS = new Set([
  'pdf', 'txt', 'djvu', 'cbr', 'cbt', 'cb7',
  'mp3', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav', 'aac',
]);

const FORMAT_PRIORITY = [
  'epub', 'kepub', 'fb2', 'mobi', 'azw3', 'azw', 'cbz',
  'pdf', 'txt', 'cbr', 'cbt', 'cb7', 'djvu',
  'm4b', 'm4a', 'mp3', 'flac', 'ogg', 'opus', 'wav', 'aac',
];

export function getPrimaryReadTarget(id: number | string, formats: string[]): string | null {
  const available = new Set(formats.map((format) => format.toLowerCase()));
  const selected = FORMAT_PRIORITY.find((format) => available.has(format));
  return selected ? `/view/${id}/${selected}` : null;
}

export function isReadableFormat(format: string): boolean {
  const normalized = format.toLowerCase();
  return FOLIATE_READER_FORMATS.has(normalized) || NATIVE_READER_FORMATS.has(normalized);
}
