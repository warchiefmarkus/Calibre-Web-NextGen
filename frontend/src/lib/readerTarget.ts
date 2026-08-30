export const FOLIATE_READER_FORMATS = new Set([
  'epub', 'kepub', 'fb2', 'fbz', 'mobi', 'azw', 'azw3', 'cbz',
]);

export const SERVER_READABLE_FORMATS = [
  'pdf', 'txt', 'djvu', 'djv', 'cbr', 'cbt', 'cb7',
  'mp3', 'mp4', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav', 'aac',
] as const;

const NATIVE_READER_FORMATS = new Set<string>(SERVER_READABLE_FORMATS);

const FORMAT_PRIORITY = [
  'epub', 'kepub', 'fb2', 'fbz', 'mobi', 'azw3', 'azw', 'cbz',
  'pdf', 'txt', 'cbr', 'cbt', 'cb7', 'djvu', 'djv',
  'm4b', 'm4a', 'mp3', 'mp4', 'flac', 'ogg', 'opus', 'wav', 'aac',
];

export function getPrimaryReadTarget(
  id: number | string,
  formats: string[],
  canRead: boolean,
): string | null {
  if (!canRead) return null;
  const available = new Set(formats.map((format) => format.toLowerCase()));
  const selected = FORMAT_PRIORITY.find((format) => available.has(format));
  return selected ? `/view/${id}/${selected}` : null;
}

export function isReadableFormat(format: string): boolean {
  const normalized = format.toLowerCase();
  return FOLIATE_READER_FORMATS.has(normalized) || NATIVE_READER_FORMATS.has(normalized);
}

/**
 * Resolve the viewer-gated archive URL used by epub.js.
 *
 * `contentUrl` was added to the detail API together with the viewer/download
 * split. During a rolling deployment, the new SPA can briefly receive a payload
 * from an older worker that does not have that field yet. Deriving the
 * established `/show` route keeps reading available without ever falling back
 * to the download-gated URL.
 */
export function getReaderContentUrl(
  id: number | string,
  format: string,
  contentUrl?: string,
): string {
  return contentUrl || `/show/${id}/${format.toLowerCase()}`;
}
