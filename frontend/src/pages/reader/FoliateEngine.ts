// Foliate browser engine boundary for the unified reflowable reader.
// Keep vendor-specific types and custom-element creation out of Reader.tsx.
import '../../vendor/foliate-js/view.js';

export type TocItem = { label?: string; href?: string; subitems?: TocItem[] };
export type SearchExcerpt = { pre: string; match: string; post: string };
export type SearchResult = { cfi: string; label: string; excerpt: SearchExcerpt };

export type FoliateLocation = {
  fraction?: number;
  location?: { current?: number; total?: number };
  tocItem?: { label?: string; href?: string };
  pageItem?: { label?: string };
  cfi?: string;
  range?: Range;
};

export type FoliateAnnotation = {
  value: string;
  color?: string;
  note?: string | null;
  id?: string;
  text?: string | null;
  unanchored?: boolean;
  sourceLabel?: string | null;
};

export type FoliateRenderer = HTMLElement & {
  setStyles?: (css: string | [string, string]) => void;
  getContents?: () => Array<{ doc: Document; index: number }>;
  page?: number;
  pages?: number;
  size?: number;
  start?: number;
  end?: number;
  viewSize?: number;
};

export type FoliateView = HTMLElement & {
  book?: {
    toc?: TocItem[];
    sections?: unknown[];
    metadata?: { title?: unknown; author?: unknown; language?: string | string[] };
    dir?: string;
  };
  renderer?: FoliateRenderer;
  lastLocation?: FoliateLocation;
  open: (file: File) => Promise<void>;
  init: (options: { lastLocation?: string | { fraction: number }; showTextStart?: boolean }) => Promise<void>;
  close: () => void;
  prev: (distance?: number) => Promise<void>;
  next: (distance?: number) => Promise<void>;
  goLeft: () => Promise<void>;
  goRight: () => Promise<void>;
  goTo: (target: string | number | { fraction: number }) => Promise<unknown>;
  goToFraction: (fraction: number) => Promise<void>;
  getSectionFractions: () => number[];
  getCFI: (index: number, range?: Range) => string;
  search: (options: Record<string, unknown>) => AsyncGenerator<unknown>;
  clearSearch: () => void;
  addAnnotation: (annotation: FoliateAnnotation) => Promise<unknown>;
  deleteAnnotation: (annotation: FoliateAnnotation) => Promise<unknown>;
  showAnnotation: (annotation: FoliateAnnotation) => Promise<void>;
  deselect: () => void;
};

export function createFoliateView(className: string): FoliateView {
  const view = document.createElement('foliate-view') as FoliateView;
  view.className = className;
  return view;
}


const MIME: Record<string, string> = {
  EPUB: 'application/epub+zip', KEPUB: 'application/epub+zip',
  FB2: 'application/x-fictionbook+xml', FBZ: 'application/x-zip-compressed-fb2',
  MOBI: 'application/x-mobipocket-ebook', AZW: 'application/vnd.amazon.ebook',
  AZW3: 'application/vnd.amazon.ebook', CBZ: 'application/vnd.comicbook+zip',
};
const SCROLLED_PAGE_OVERLAP_RATIO = 0.12;

export function readerMime(format: string): string { return MIME[format] ?? ''; }
export function readerFileName(format: string): string {
  return `book.${format === 'KEPUB' ? 'epub' : format.toLowerCase()}`;
}
export function scrolledPageTurnDistance(renderer: FoliateRenderer | undefined): number | undefined {
  if (!renderer || renderer.getAttribute('flow') !== 'scrolled') return undefined;
  const size = Number(renderer.size);
  if (!Number.isFinite(size) || size <= 0) return undefined;
  const margin = Math.max(0, Number.parseFloat(renderer.getAttribute('margin') ?? '0') || 0);
  const visibleSize = Math.max(1, size - 2 * margin);
  return Math.max(1, visibleSize - Math.max(24, visibleSize * SCROLLED_PAGE_OVERLAP_RATIO));
}
export function allowsWheelPageTurn(target: EventTarget | null): boolean {
  const element = target as { closest?: (selector: string) => Element | null } | null;
  if (!element?.closest) return true;
  if (element.closest('[data-reader-wheel-page-zone]')) return true;
  return !element.closest('input, textarea, select, button, [contenteditable="true"], [role="slider"]');
}
export function isReaderTypingTarget(target: EventTarget | null): boolean {
  const element = target as { closest?: (selector: string) => Element | null } | null;
  return !!element?.closest?.(
    'textarea, select, [contenteditable="true"], input:not([type="checkbox"]):not([type="radio"]):not([type="range"])',
  );
}
export function formatLanguageMap(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    return Object.values(value as Record<string, unknown>)
      .find((item): item is string => typeof item === 'string') ?? '';
  }
  return '';
}
export function flattenToc(items: TocItem[] = [], depth = 0): Array<TocItem & { depth: number }> {
  return items.flatMap((item) => [{ ...item, depth }, ...flattenToc(item.subitems ?? [], depth + 1)]);
}
