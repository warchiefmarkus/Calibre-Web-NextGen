import { Fragment, createElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Link } from 'wouter';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faOpenai } from '@fortawesome/free-brands-svg-icons';
import {
  AlignJustify, Bookmark, BookOpen, ChevronLeft, ChevronRight,
  Columns2, Highlighter, Languages, List, Maximize, Search, Settings,
  Square, StickyNote, Trash2, Volume2, X,
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import { apiDelete, apiGet, apiPatch, apiPost, resourceUrl } from '../lib/api';
import {
  useBook, useBookmark, useCreateReaderBookmark, useDeleteReaderBookmark,
  useReaderBookmarks, useReaderSettings, useReaderTranslationProfiles,
  useSaveReaderSettings, translateReaderPage, type ReaderBookmark, type ReaderSettings,
  type ReaderTranslationBlock, type ReaderTranslationResponse, type ReaderTranslationRun,
  type ReaderTranslationRunMark,
} from '../lib/queries';
import { useT } from '../lib/i18n';
import { parseFb2ScrollBookmark, useReadingPositionSaver } from '../lib/readerProgress';
import { ReaderTranslationSettings } from './ReaderTranslationSettings';
import styles from './Reader.module.css';

// Vendored at an exact upstream commit; see vendor/foliate-js/UPSTREAM.md.
import '../vendor/foliate-js/view.js';
// @ts-expect-error foliate-js intentionally ships browser JavaScript without declarations.
import { Overlayer } from '../vendor/foliate-js/overlayer.js';
type TocItem = { label?: string; href?: string; subitems?: TocItem[] };
type SearchExcerpt = { pre: string; match: string; post: string };
type SearchResult = { cfi: string; label: string; excerpt: SearchExcerpt };
type ReaderPanel = 'toc' | 'search' | 'bookmarks' | 'notes' | 'settings' | 'translation' | null;

type FoliateLocation = {
  fraction?: number;
  location?: { current?: number; total?: number };
  tocItem?: { label?: string; href?: string };
  pageItem?: { label?: string };
  cfi?: string;
  range?: Range;
};

type FoliateAnnotation = {
  value: string;
  color?: string;
  note?: string | null;
  id?: string;
  text?: string | null;
};

type FoliateRenderer = HTMLElement & {
  setStyles?: (css: string | [string, string]) => void;
  getContents?: () => Array<{ doc: Document; index: number }>;
  page?: number;
  pages?: number;
  size?: number;
};
type FoliateView = HTMLElement & {
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
  prev: () => Promise<void>;
  next: () => Promise<void>;
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

type ServerAnnotation = {
  annotation_id: string;
  cfi_range: string | null;
  highlighted_text: string | null;
  highlight_color: string;
  note_text: string | null;
};
type PendingReaderSelection = {
  value: string;
  text: string;
  leadingWhitespace: string;
  trailingWhitespace: string;
};

type InlineTranslationPatch = {
  marker: HTMLElement;
  original: DocumentFragment;
};
const FOLIATE_FORMATS = ['EPUB', 'KEPUB', 'FB2', 'FBZ', 'MOBI', 'AZW3', 'AZW', 'CBZ'] as const;
const FORMAT_PRIORITY = ['EPUB', 'KEPUB', 'FB2', 'MOBI', 'AZW3', 'AZW', 'CBZ'];
const FONT_MIN = 75;
const FONT_MAX = 200;

const MIME: Record<string, string> = {
  EPUB: 'application/epub+zip',
  KEPUB: 'application/epub+zip',
  FB2: 'application/x-fictionbook+xml',
  FBZ: 'application/x-zip-compressed-fb2',
  MOBI: 'application/x-mobipocket-ebook',
  AZW: 'application/vnd.amazon.ebook',
  AZW3: 'application/vnd.amazon.ebook',
  CBZ: 'application/vnd.comicbook+zip',
};

const FONT_FAMILY: Record<ReaderSettings['font'], string> = {
  default: 'Georgia, "Times New Roman", serif',
  Yahei: '"Microsoft YaHei", sans-serif',
  SimSun: 'SimSun, serif',
  KaiTi: 'KaiTi, serif',
  Arial: 'Arial, sans-serif',
};

const THEME: Record<ReaderSettings['theme'], { background: string; text: string; link: string }> = {
  lightTheme: { background: '#fffdf8', text: '#24211d', link: '#225ea8' },
  sepiaTheme: { background: '#f4ecd8', text: '#433422', link: '#7a4b20' },
  darkTheme: { background: '#202124', text: '#e8eaed', link: '#8ab4f8' },
  blackTheme: { background: '#000000', text: '#eeeeee', link: '#8ab4f8' },
};

const READER_WHEEL_THRESHOLD_PX = 48;
const READER_WHEEL_COOLDOWN_MS = 320;
const READER_WHEEL_IDLE_RESET_MS = 160;

function allowsWheelPageTurn(target: EventTarget | null): boolean {
  const element = target as { closest?: (selector: string) => Element | null } | null;
  if (!element?.closest) return true;
  if (element.closest('[data-reader-wheel-page-zone]')) return true;
  return !element.closest('input, textarea, select, button, [contenteditable="true"], [role="slider"]');
}

function isReaderTypingTarget(target: EventTarget | null): boolean {
  const element = target as { closest?: (selector: string) => Element | null } | null;
  return !!element?.closest?.(
    'textarea, select, [contenteditable="true"], input:not([type="checkbox"]):not([type="radio"]):not([type="range"])',
  );
}

const TRANSLATABLE_SELECTOR = 'h1, h2, h3, h4, h5, h6, p, li, blockquote, pre, figcaption';
const TRANSLATION_CONTENT_SELECTOR = `${TRANSLATABLE_SELECTOR}, img`;
const MAX_VISIBLE_TRANSLATION_CHARS = 8_000;
const MAX_VISIBLE_TRANSLATION_BLOCKS = 80;
const MAX_INLINE_TRANSLATION_RUNS = 200;
const MAX_INLINE_TRANSLATION_RUN_CHARS = 1200;
const TRANSLATION_DEBOUNCE_MS = 500;

type TranslationViewport = {
  frameRect: DOMRect;
  rendererRect: DOMRect;
  viewportRect: { left: number; right: number; top: number; bottom: number };
};

type TranslationBlockStyle = Pick<CSSProperties,
  'fontFamily' | 'fontSize' | 'fontWeight' | 'fontStyle' | 'lineHeight'
  | 'letterSpacing' | 'textAlign' | 'textIndent' | 'textTransform'
  | 'marginBlockStart' | 'marginBlockEnd'>;

type SourceTranslationRun = ReaderTranslationRun & { href?: string };
type StyledTranslationBlock = ReaderTranslationBlock & {
  style?: TranslationBlockStyle;
  sourceRuns?: SourceTranslationRun[];
};

type PreservedImageStyle = Pick<CSSProperties,
  'width' | 'height' | 'maxWidth' | 'maxHeight' | 'objectFit' | 'display'
  | 'marginBlockStart' | 'marginBlockEnd' | 'marginInlineStart' | 'marginInlineEnd'
  | 'borderRadius'>;

type TranslationTextSegment = { kind: 'text'; id: string };
type TranslationImageSegment = {
  kind: 'image';
  id: string;
  src: string;
  alt: string;
  title?: string;
  style: PreservedImageStyle;
};
type TranslationContentSegment = TranslationTextSegment | TranslationImageSegment;

type TranslationPageLayout = {
  pageWidth: number;
  pageHeight: number;
  contentWidth: number;
  contentHeight: number;
  paddingInlineStart: number;
  paddingInlineEnd: number;
  paddingBlockStart: number;
  paddingBlockEnd: number;
  columnGap: number;
  defaultStyle: TranslationBlockStyle;
};

type VisibleTranslationPage = {
  blocks: ReaderTranslationBlock[];
  styles: Record<string, TranslationBlockStyle>;
  sourceRuns: Record<string, SourceTranslationRun[]>;
  segments: TranslationContentSegment[];
  layout: TranslationPageLayout;
};

type TranslationPreloadJob = {
  key: string;
  settings: ReaderSettings;
  blocks: ReaderTranslationBlock[];
};

type TranslationPreloadTask = {
  key: string;
  controller: AbortController;
  promise: Promise<ReaderTranslationResponse>;
};

const LANGUAGE_ALIASES: Record<string, string> = {
  ua: 'uk', ukr: 'uk', ukrainian: 'uk',
  eng: 'en', english: 'en',
  rus: 'ru', russian: 'ru',
  pol: 'pl', polish: 'pl',
  deu: 'de', ger: 'de', german: 'de',
  fra: 'fr', fre: 'fr', french: 'fr',
};

function chatGptSelectedTextUrl(text: string): string {
  return `https://chatgpt.com/?q=${encodeURIComponent(text.trim())}`;
}

function normalizeLanguageCode(value: unknown): string {
  const raw = Array.isArray(value) ? value[0] : value;
  if (typeof raw !== 'string') return '';
  const normalized = raw.trim().toLowerCase().replace('_', '-');
  if (!normalized) return '';
  const primary = normalized.split('-')[0];
  return LANGUAGE_ALIASES[normalized] ?? LANGUAGE_ALIASES[primary] ?? primary;
}

function canExtractTranslationPageOffset(renderer: FoliateRenderer, pageOffset: number): boolean {
  if (pageOffset === 0) return true;
  if (pageOffset !== 1 || renderer.getAttribute('flow') !== 'paginated') return false;
  const page = Number(renderer.page);
  const pages = Number(renderer.pages);
  const size = Number(renderer.size);
  return Number.isFinite(page) && Number.isFinite(pages) && Number.isFinite(size)
    && size > 0 && page + pageOffset <= pages - 2;
}

function translationViewport(
  renderer: FoliateRenderer, doc: Document, pageOffset = 0,
): TranslationViewport | null {
  const frame = doc.defaultView?.frameElement;
  if (!(frame instanceof HTMLElement) || !canExtractTranslationPageOffset(renderer, pageOffset)) return null;
  const rendererRect = renderer.getBoundingClientRect();
  const frameRect = frame.getBoundingClientRect();
  if (renderer.getAttribute('flow') !== 'paginated') {
    return {
      frameRect,
      rendererRect,
      viewportRect: {
        left: rendererRect.left, right: rendererRect.right,
        top: rendererRect.top, bottom: rendererRect.bottom,
      },
    };
  }

  // Foliate lays an entire section out as one very wide iframe. The iframe's
  // innerWidth is therefore the width of every column, not the visible page.
  // Limit extraction to the centred current page/spread. For preloading, shift
  // only the extraction viewport by one paginator step; the renderer, CFI, and
  // saved reading position remain untouched.
  const writingMode = doc.defaultView?.getComputedStyle(doc.documentElement).writingMode ?? '';
  if (pageOffset && writingMode.startsWith('vertical')) return null;
  const pageWidth = Math.max(1, doc.documentElement.getBoundingClientRect().width);
  const maxColumns = Math.max(1, Number(renderer.getAttribute('max-column-count') || 1));
  const visibleWidth = Math.min(rendererRect.width, pageWidth * maxColumns);
  const direction = renderer.getAttribute('dir') === 'rtl' ? -1 : 1;
  const shift = pageOffset * Math.max(1, Number(renderer.size) || rendererRect.width) * direction;
  const left = rendererRect.left + (rendererRect.width - visibleWidth) / 2 + shift;
  return {
    frameRect,
    rendererRect,
    viewportRect: {
      left,
      right: left + visibleWidth,
      top: rendererRect.top,
      bottom: rendererRect.bottom,
    },
  };
}

function rectIntersectsViewport(rect: DOMRect, viewport: TranslationViewport): boolean {
  const left = viewport.frameRect.left + rect.left;
  const right = viewport.frameRect.left + rect.right;
  const top = viewport.frameRect.top + rect.top;
  const bottom = viewport.frameRect.top + rect.bottom;
  return rect.width > 0 && rect.height > 0
    && right > viewport.viewportRect.left && left < viewport.viewportRect.right
    && bottom > viewport.viewportRect.top && top < viewport.viewportRect.bottom;
}

function intersectingOuterRects(element: Element, viewport: TranslationViewport): Array<{
  left: number; right: number; top: number; bottom: number;
}> {
  return Array.from(element.getClientRects())
    .filter((rect) => rectIntersectsViewport(rect, viewport))
    .map((rect) => ({
      left: viewport.frameRect.left + rect.left,
      right: viewport.frameRect.left + rect.right,
      top: viewport.frameRect.top + rect.top,
      bottom: viewport.frameRect.top + rect.bottom,
    }));
}

type ExtractedInlineRuns = {
  text: string;
  sourceRuns: SourceTranslationRun[];
  requestRuns: ReaderTranslationRun[];
};

function inlineRunContext(node: Text, block: Element): {
  marks: ReaderTranslationRunMark[];
  href?: string;
} {
  const ancestors: Element[] = [];
  let current = node.parentElement;
  while (current && current !== block) {
    ancestors.push(current);
    current = current.parentElement;
  }
  ancestors.reverse();

  const marks: ReaderTranslationRunMark[] = [];
  const addMark = (mark: ReaderTranslationRunMark) => {
    if (!marks.includes(mark)) marks.push(mark);
  };
  let href: string | undefined;
  for (const element of ancestors) {
    const name = element.localName.toLowerCase();
    if (name === 'strong' || name === 'b') addMark('strong');
    if (name === 'em' || name === 'i') addMark('em');
    if (['code', 'kbd', 'samp', 'tt'].includes(name)) addMark('code');
    if (name === 'sup') addMark('sup');
    if (name === 'sub') addMark('sub');
    if (name === 'a') {
      addMark('link');
      const resolved = (element as Element & { href?: string }).href || element.getAttribute('href') || '';
      if (resolved) href = resolved;
    }

    const style = element.ownerDocument.defaultView?.getComputedStyle(element);
    if (!style) continue;
    const numericWeight = Number.parseInt(style.fontWeight, 10);
    if (style.fontWeight === 'bold' || (Number.isFinite(numericWeight) && numericWeight >= 600)) {
      addMark('strong');
    }
    if (style.fontStyle === 'italic' || style.fontStyle === 'oblique') addMark('em');
    if (style.verticalAlign === 'super') addMark('sup');
    if (style.verticalAlign === 'sub') addMark('sub');
  }
  return { marks, href };
}

function sameInlineRunFormat(
  left: Omit<SourceTranslationRun, 'id' | 'text'>,
  right: Omit<SourceTranslationRun, 'id' | 'text'>,
): boolean {
  return left.href === right.href
    && (left.break_before ?? 0) === (right.break_before ?? 0)
    && JSON.stringify(left.marks ?? []) === JSON.stringify(right.marks ?? []);
}

function visibleBlockRuns(
  element: Element,
  viewport: TranslationViewport,
  blockId: string,
  maxChars: number,
): ExtractedInlineRuns | null {
  const doc = element.ownerDocument;
  const runs: Array<Omit<SourceTranslationRun, 'id'>> = [];
  let pendingBreaks = 0;
  let pendingSpace = false;
  let totalChars = 0;
  let stopped = false;

  const appendText = (raw: string, context: { marks: ReaderTranslationRunMark[]; href?: string }) => {
    if (stopped) return;
    let text = raw.replace(/[\t\f\v ]+/g, ' ');
    if (!text.trim()) return;
    const remaining = maxChars - totalChars;
    if (remaining <= 0) {
      stopped = true;
      return;
    }
    if (text.length > remaining) {
      text = text.slice(0, remaining).trimEnd();
      stopped = true;
    }
    if (!text) return;

    const format: Omit<SourceTranslationRun, 'id' | 'text'> = {
      marks: context.marks.length ? context.marks : undefined,
      href: context.href,
      break_before: pendingBreaks || undefined,
    };
    const last = runs[runs.length - 1];
    const canMerge = !!last
      && !pendingBreaks
      && sameInlineRunFormat(last, format)
      && last.text.length + text.length <= MAX_INLINE_TRANSLATION_RUN_CHARS;
    if (canMerge) {
      last.text += text;
    } else if (runs.length < MAX_INLINE_TRANSLATION_RUNS) {
      runs.push({ text, ...format });
    } else {
      stopped = true;
      return;
    }
    totalChars += text.length;
    pendingBreaks = 0;
  };

  const visit = (node: Node) => {
    if (stopped) return;
    if (node.nodeType === Node.TEXT_NODE) {
      const textNode = node as Text;
      const parent = textNode.parentElement;
      if (!parent || parent.closest('script, style, noscript, svg, canvas')) return;
      const context = inlineRunContext(textNode, element);
      let previousEnd = 0;
      for (const match of textNode.data.matchAll(/\S+/g)) {
        const start = match.index ?? 0;
        const end = start + match[0].length;
        const whitespaceBefore = textNode.data.slice(previousEnd, start);
        previousEnd = end;
        const range = doc.createRange();
        range.setStart(textNode, start);
        range.setEnd(textNode, end);
        if (!Array.from(range.getClientRects()).some((rect) => rectIntersectsViewport(rect, viewport))) {
          continue;
        }
        const lineBreaks = whitespaceBefore.match(/\r\n|\r|\n/g)?.length ?? 0;
        if (lineBreaks) {
          pendingBreaks = Math.min(8, pendingBreaks + lineBreaks);
          pendingSpace = false;
        } else if (/\s/.test(whitespaceBefore)) {
          pendingSpace = true;
        }
        const prefix = pendingSpace && runs.length ? ' ' : '';
        pendingSpace = false;
        appendText(`${prefix}${match[0]}`, context);
        if (stopped) break;
      }
      const trailingWhitespace = textNode.data.slice(previousEnd);
      const trailingBreaks = trailingWhitespace.match(/\r\n|\r|\n/g)?.length ?? 0;
      if (trailingBreaks) {
        pendingBreaks = Math.min(8, pendingBreaks + trailingBreaks);
        pendingSpace = false;
      } else if (/\s/.test(trailingWhitespace)) {
        pendingSpace = true;
      }
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const child = node as Element;
    const name = child.localName.toLowerCase();
    if (name === 'br') {
      const range = doc.createRange();
      range.selectNode(child);
      if (Array.from(range.getClientRects()).some((rect) => rectIntersectsViewport(rect, viewport))) {
        pendingBreaks = Math.min(8, pendingBreaks + 1);
        pendingSpace = false;
      }
      return;
    }
    if (['script', 'style', 'noscript', 'svg', 'canvas', 'img'].includes(name)) return;
    child.childNodes.forEach(visit);
  };

  element.childNodes.forEach(visit);
  while (runs.length && !runs[0].text.trim()) runs.shift();
  while (runs.length && !runs[runs.length - 1].text.trim()) runs.pop();
  if (!runs.length) return null;
  runs[0].text = runs[0].text.replace(/^\s+/, '');
  runs[runs.length - 1].text = runs[runs.length - 1].text.replace(/\s+$/, '');

  const sourceRuns = runs
    .filter((run) => run.text.trim())
    .map((run, index) => ({ ...run, id: `${blockId}-r${index + 1}` }));
  if (!sourceRuns.length) return null;
  const requestRuns: ReaderTranslationRun[] = sourceRuns.map(({ href: _href, ...run }) => run);
  const text = sourceRuns.map((run) => `${'\n'.repeat(run.break_before ?? 0)}${run.text}`).join('').trim();
  return { text, sourceRuns, requestRuns };
}


function computedTranslationStyle(element: Element): TranslationBlockStyle {
  const style = element.ownerDocument.defaultView?.getComputedStyle(element);
  if (!style) return {};
  return {
    fontFamily: style.fontFamily,
    fontSize: style.fontSize,
    fontWeight: style.fontWeight,
    fontStyle: style.fontStyle,
    lineHeight: style.lineHeight,
    letterSpacing: style.letterSpacing,
    textAlign: style.textAlign as CSSProperties['textAlign'],
    textIndent: style.textIndent,
    textTransform: style.textTransform as CSSProperties['textTransform'],
    marginBlockStart: style.marginBlockStart || style.marginTop,
    marginBlockEnd: style.marginBlockEnd || style.marginBottom,
  };
}

function preservedImageSegment(
  image: HTMLImageElement, viewport: TranslationViewport, id: string,
): TranslationImageSegment | null {
  const rects = intersectingOuterRects(image, viewport);
  const src = image.currentSrc || image.src;
  if (!rects.length || !src) return null;
  const rect = rects.reduce((largest, candidate) => {
    const largestArea = (largest.right - largest.left) * (largest.bottom - largest.top);
    const candidateArea = (candidate.right - candidate.left) * (candidate.bottom - candidate.top);
    return candidateArea > largestArea ? candidate : largest;
  });
  const style = image.ownerDocument.defaultView?.getComputedStyle(image);
  const parentStyle = image.parentElement
    ? image.ownerDocument.defaultView?.getComputedStyle(image.parentElement)
    : null;
  const width = Math.max(1, rect.right - rect.left);
  const height = Math.max(1, rect.bottom - rect.top);
  const centered = parentStyle?.textAlign === 'center'
    || (style?.marginLeft === 'auto' && style?.marginRight === 'auto');
  return {
    kind: 'image',
    id,
    src,
    alt: image.alt || '',
    title: image.title || undefined,
    style: {
      width: `${width}px`,
      height: 'auto',
      maxWidth: '100%',
      maxHeight: `${Math.max(1, Math.min(height, viewport.rendererRect.height - 16))}px`,
      objectFit: (style?.objectFit || 'contain') as CSSProperties['objectFit'],
      display: 'block',
      marginBlockStart: style?.marginBlockStart || style?.marginTop || '0px',
      marginBlockEnd: style?.marginBlockEnd || style?.marginBottom || '0px',
      marginInlineStart: centered ? 'auto' : (style?.marginInlineStart || style?.marginLeft || '0px'),
      marginInlineEnd: centered ? 'auto' : (style?.marginInlineEnd || style?.marginRight || '0px'),
      borderRadius: style?.borderRadius || '0px',
    },
  };
}

function clampLayoutInset(value: number, pageSize: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(0, Math.min(pageSize * 0.3, value));
}

function extractVisiblePage(renderer?: FoliateRenderer, pageOffset = 0): VisibleTranslationPage | null {
  if (!renderer) return null;
  const contents = renderer.getContents?.() ?? [];
  const blocks: ReaderTranslationBlock[] = [];
  const styles: Record<string, TranslationBlockStyle> = {};
  const sourceRuns: Record<string, SourceTranslationRun[]> = {};
  const segments: TranslationContentSegment[] = [];
  let totalChars = 0;
  let layoutViewport: TranslationViewport | null = null;
  let layoutDoc: Document | null = null;
  let minTextLeft = Number.POSITIVE_INFINITY;
  let maxTextRight = Number.NEGATIVE_INFINITY;

  for (const content of [...contents].sort((a, b) => a.index - b.index)) {
    const doc = content.doc;
    const viewport = translationViewport(renderer, doc, pageOffset);
    if (!viewport) continue;
    layoutViewport ??= viewport;
    layoutDoc ??= doc;
    const elements = Array.from(doc.body?.querySelectorAll(TRANSLATION_CONTENT_SELECTOR) ?? []);
    for (let index = 0; index < elements.length; index += 1) {
      const element = elements[index];
      if (element.localName.toLowerCase() === 'img') {
        const image = preservedImageSegment(
          element as HTMLImageElement, viewport, `d${content.index}-i${index}`,
        );
        if (image) segments.push(image);
        continue;
      }
      if (blocks.length >= MAX_VISIBLE_TRANSLATION_BLOCKS || totalChars >= MAX_VISIBLE_TRANSLATION_CHARS) break;
      const visibleRects = intersectingOuterRects(element, viewport);
      if (!visibleRects.length) continue;
      if (Array.from(element.children).some((child) => child.matches(TRANSLATABLE_SELECTOR))) continue;
      const blockId = `d${content.index}-b${index}`;
      const remaining = MAX_VISIBLE_TRANSLATION_CHARS - totalChars;
      const extracted = visibleBlockRuns(element, viewport, blockId, remaining);
      if (!extracted) continue;
      blocks.push({
        id: blockId,
        tag: element.tagName.toLowerCase(),
        text: extracted.text,
        runs: extracted.requestRuns,
      });
      sourceRuns[blockId] = extracted.sourceRuns;
      segments.push({ kind: 'text', id: blockId });
      styles[blockId] = computedTranslationStyle(element);
      totalChars += extracted.requestRuns.reduce((sum, run) => sum + run.text.length, 0);
      for (const rect of visibleRects) {
        minTextLeft = Math.min(minTextLeft, rect.left);
        maxTextRight = Math.max(maxTextRight, rect.right);
      }
    }
    if (blocks.length >= MAX_VISIBLE_TRANSLATION_BLOCKS || totalChars >= MAX_VISIBLE_TRANSLATION_CHARS) break;
  }

  if (!segments.length || !layoutViewport || !layoutDoc) return null;
  const { rendererRect, frameRect, viewportRect } = layoutViewport;
  const pageWidth = Math.max(1, viewportRect.right - viewportRect.left);
  const pageHeight = Math.max(1, rendererRect.height);
  const fallbackInline = Math.min(32, pageWidth * 0.06);
  const paddingInlineStart = clampLayoutInset(
    minTextLeft - viewportRect.left, pageWidth, fallbackInline,
  );
  const paddingInlineEnd = clampLayoutInset(
    viewportRect.right - maxTextRight, pageWidth, fallbackInline,
  );
  const paddingBlockStart = clampLayoutInset(
    frameRect.top - rendererRect.top, pageHeight, 16,
  );
  const paddingBlockEnd = clampLayoutInset(
    rendererRect.bottom - frameRect.bottom, pageHeight, 16,
  );
  const contentWidth = Math.max(160, pageWidth - paddingInlineStart - paddingInlineEnd);
  const contentHeight = Math.max(120, pageHeight - paddingBlockStart - paddingBlockEnd);
  const body = layoutDoc.body ?? layoutDoc.documentElement;

  return {
    blocks,
    styles,
    sourceRuns,
    segments,
    layout: {
      pageWidth,
      pageHeight,
      contentWidth,
      contentHeight,
      paddingInlineStart,
      paddingInlineEnd,
      paddingBlockStart,
      paddingBlockEnd,
      columnGap: Math.max(16, paddingInlineStart + paddingInlineEnd),
      defaultStyle: computedTranslationStyle(body),
    },
  };
}

function looksLikeUkrainian(blocks: ReaderTranslationBlock[]): boolean {
  const text = blocks.map((block) => block.text).join(' ');
  const cyrillic = text.match(/[А-Яа-яІіЇїЄєҐґЁёЫыЭэЪъ]/g)?.length ?? 0;
  if (cyrillic < 40) return false;
  const ukrainianLetters = text.match(/[ІіЇїЄєҐґ]/g)?.length ?? 0;
  const russianExclusive = text.match(/[ЁёЫыЭэЪъ]/g)?.length ?? 0;
  const words = text.toLowerCase().match(/[а-яіїєґ']+/g) ?? [];
  const ukrainianWords = new Set(['і', 'та', 'що', 'це', 'як', 'для', 'який', 'яка', 'які', 'бути', 'від', 'після']);
  const wordHits = words.filter((word) => ukrainianWords.has(word)).length;
  return (ukrainianLetters >= 3 && ukrainianLetters >= russianExclusive * 2 + 1)
    || (wordHits >= 4 && russianExclusive === 0);
}

function sourceAlreadyMatchesTarget(
  settings: ReaderSettings,
  bookLanguage: string,
  blocks?: ReaderTranslationBlock[],
): boolean {
  const target = normalizeLanguageCode(settings.translationTargetLanguage);
  const configuredSource = normalizeLanguageCode(settings.translationSourceLanguage);
  if (!target) return false;
  if (configuredSource && configuredSource !== 'auto') return configuredSource === target;
  if (bookLanguage && bookLanguage === target) return true;
  return target === 'uk' && !!blocks?.length && looksLikeUkrainian(blocks);
}

function translationPageKey(settings: ReaderSettings, blocks: ReaderTranslationBlock[]): string {
  return JSON.stringify({
    profile: settings.translationProfileId,
    source: settings.translationSourceLanguage,
    target: settings.translationTargetLanguage,
    prompt: settings.translationPrompt,
    blocks: blocks.map((block) => [block.id, block.text, block.runs]),
  });
}

function safeTranslationHref(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value, window.location.href);
    return ['http:', 'https:', 'mailto:', 'tel:', 'blob:'].includes(url.protocol)
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

function translatedInlineContent(block: StyledTranslationBlock): ReactNode {
  const sourceRuns = block.sourceRuns;
  const translatedRuns = block.runs;
  if (!sourceRuns?.length || !translatedRuns?.length) return block.text;
  const translatedById = new Map(translatedRuns.map((run) => [run.id, run.text]));
  return sourceRuns.map((sourceRun) => {
    const translatedText = translatedById.get(sourceRun.id) ?? sourceRun.text;
    let node: ReactNode = translatedText;
    const formattingMarks = (sourceRun.marks ?? []).filter((mark) => mark !== 'link');
    for (const mark of [...formattingMarks].reverse()) {
      node = createElement(mark, undefined, node);
    }
    if (sourceRun.marks?.includes('link')) {
      const href = safeTranslationHref(sourceRun.href);
      node = href
        ? createElement('a', { href, target: '_blank', rel: 'noopener noreferrer' }, node)
        : createElement('span', { className: styles.translationLink }, node);
    }
    const children: ReactNode[] = [];
    for (let index = 0; index < (sourceRun.break_before ?? 0); index += 1) {
      children.push(createElement('br', { key: `${sourceRun.id}-br${index}` }));
    }
    children.push(node);
    return createElement(Fragment, { key: sourceRun.id }, ...children);
  });
}

function formatLanguageMap(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    const entries = Object.values(value as Record<string, unknown>);
    return entries.find((item): item is string => typeof item === 'string') ?? '';
  }
  return '';
}

function flattenToc(items: TocItem[] = [], depth = 0): Array<TocItem & { depth: number }> {
  return items.flatMap((item) => [
    { ...item, depth },
    ...flattenToc(item.subitems ?? [], depth + 1),
  ]);
}

function excerptText(excerpt: SearchExcerpt): string {
  return `${excerpt.pre}${excerpt.match}${excerpt.post}`;
}

function fileName(format: string): string {
  const extension = format === 'KEPUB' ? 'epub' : format.toLowerCase();
  return `book.${extension}`;
}

function readerCss(settings: ReaderSettings, compactViewport = false): string {
  const theme = THEME[settings.theme];
  const compactReflow = compactViewport ? `
    html {
      inline-size: 100% !important;
      min-inline-size: 0 !important;
      max-inline-size: 100% !important;
      overflow-x: hidden !important;
      box-sizing: border-box !important;
    }
    body {
      inline-size: auto !important;
      width: auto !important;
      min-inline-size: 0 !important;
      min-width: 0 !important;
      max-inline-size: 100% !important;
      max-width: 100% !important;
      margin-inline: 0 !important;
      overflow-x: hidden !important;
      box-sizing: border-box !important;
    }
    body * {
      min-inline-size: 0 !important;
      min-width: 0 !important;
      max-inline-size: 100% !important;
      max-width: 100% !important;
      box-sizing: border-box !important;
    }
    h1, h2, h3, h4, h5, h6, p, pre, code {
      overflow-wrap: anywhere !important;
    }
    pre, code { white-space: pre-wrap !important; }
    table { inline-size: 100% !important; table-layout: fixed !important; }
  ` : '';
  const textAlignment = settings.justifyText
    ? 'body, p, li, blockquote { text-align: justify !important; text-align-last: auto !important; }'
    : 'body { text-align: left !important; }';
  return `
    :root { color-scheme: ${settings.theme === 'lightTheme' || settings.theme === 'sepiaTheme' ? 'light' : 'dark'}; }
    html, body { background: ${theme.background} !important; color: ${theme.text} !important; }
    body { font-family: ${FONT_FAMILY[settings.font]} !important; font-size: ${settings.fontSize}% !important;
      line-height: ${settings.lineHeight / 100} !important; }
    ${textAlignment}
    a { color: ${theme.link} !important; }
    img, svg, video { max-width: 100% !important; }
    ${compactReflow}
    ::selection { background: rgba(255, 214, 64, .55); }
  `;
}
export function Reader({ id, format }: { id: string; format?: string }) {
  const t = useT();
  const bookQuery = useBook(id);
  const requested = format?.toUpperCase();
  const selectedFormat = useMemo(() => {
    const formats = bookQuery.data?.formats ?? [];
    if (requested && FOLIATE_FORMATS.includes(requested as typeof FOLIATE_FORMATS[number])) {
      return formats.find((item) => item.format.toUpperCase() === requested) ?? null;
    }
    return FORMAT_PRIORITY
      .map((name) => formats.find((item) => item.format.toUpperCase() === name))
      .find(Boolean) ?? null;
  }, [bookQuery.data?.formats, requested]);

  const fmt = selectedFormat?.format.toLowerCase() ?? requested?.toLowerCase() ?? 'epub';
  const settingsQuery = useReaderSettings();
  const translationProfilesQuery = useReaderTranslationProfiles();
  const positionQuery = useBookmark(id, fmt);
  const bookmarksQuery = useReaderBookmarks(id, fmt);
  const saveSettings = useSaveReaderSettings();
  const createBookmark = useCreateReaderBookmark(id, fmt);
  const deleteBookmark = useDeleteReaderBookmark(id, fmt);

  const { schedule: schedulePosition, saveError } = useReadingPositionSaver(id, fmt, 450);

  const hostRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLElement>(null);
  const viewRef = useRef<FoliateView | null>(null);
  const searchRunRef = useRef(0);
  const annotationsRef = useRef<Map<string, FoliateAnnotation>>(new Map());
  const currentRef = useRef<FoliateLocation>({ fraction: 0 });
  const settingsRef = useRef<ReaderSettings | null>(null);
  const wheelDeltaRef = useRef(0);
  const wheelDirectionRef = useRef(0);
  const wheelLockUntilRef = useRef(0);
  const wheelIdleTimerRef = useRef<number | null>(null);
  const translationAbortRef = useRef<AbortController | null>(null);
  const translationInFlightKeyRef = useRef<string | null>(null);
  const translationCurrentKeyRef = useRef<string | null>(null);
  const translationFailureRef = useRef<{ key: string; message: string } | null>(null);
  const translationStartedAtRef = useRef<number | null>(null);
  const translationPreloadAbortRef = useRef<AbortController | null>(null);
  const translationPreloadStartedAtRef = useRef<number | null>(null);
  const translationPreloadInFlightKeyRef = useRef<string | null>(null);
  const translationPreloadQueuedRef = useRef<TranslationPreloadJob | null>(null);
  const translationPreloadTaskRef = useRef<TranslationPreloadTask | null>(null);
  const translationPreloadRunnerRef = useRef<(job: TranslationPreloadJob) => void>(() => undefined);
  const translationCacheRef = useRef<Map<string, ReaderTranslationBlock[]>>(new Map());
  const translationPagerContentRef = useRef<HTMLDivElement>(null);
  const translationBlocksRef = useRef<StyledTranslationBlock[]>([]);
  const translationPageIndexRef = useRef(0);
  const translationPageCountRef = useRef(1);
  const translationLandingRef = useRef<'first' | 'last' | null>(null);
  const translationTransitionRef = useRef(false);
  const translationSkippedRef = useRef(false);
  const pendingSelectionRangeRef = useRef<{ range: Range; doc: Document } | null>(null);
  const inlineTranslationPatchesRef = useRef<InlineTranslationPatch[]>([]);

  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<ReaderPanel>(null);
  const [title, setTitle] = useState('');
  const [bookLanguage, setBookLanguage] = useState('');
  const [toc, setToc] = useState<Array<TocItem & { depth: number }>>([]);
  const [sectionFractions, setSectionFractions] = useState<number[]>([]);
  const [location, setLocation] = useState<FoliateLocation>({ fraction: 0 });
  const [settings, setSettings] = useState<ReaderSettings | null>(null);
  const [searchText, setSearchText] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchProgress, setSearchProgress] = useState(0);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [pendingSelection, setPendingSelection] = useState<PendingReaderSelection | null>(null);
  const [annotations, setAnnotations] = useState<FoliateAnnotation[]>([]);
  const [selectedAnnotation, setSelectedAnnotation] = useState<FoliateAnnotation | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [translationBlocks, setTranslationBlocks] = useState<StyledTranslationBlock[]>([]);
  const [translationSegments, setTranslationSegments] = useState<TranslationContentSegment[]>([]);
  const [translationLayout, setTranslationLayout] = useState<TranslationPageLayout | null>(null);
  const [translationPageIndex, setTranslationPageIndex] = useState(0);
  const [translationLoading, setTranslationLoading] = useState(false);
  const [translationPreloading, setTranslationPreloading] = useState(false);
  const [translationError, setTranslationError] = useState<string | null>(null);
  const [translationRetry, setTranslationRetry] = useState(0);
  const [translationSkipped, setTranslationSkipped] = useState(false);
  const [selectionTranslationLoading, setSelectionTranslationLoading] = useState(false);
  const [selectionTranslationError, setSelectionTranslationError] = useState<string | null>(null);

  const translationRequestTimeoutMs = useMemo(() => {
    const profile = translationProfilesQuery.data?.profiles.find(
      (item) => item.id === settings?.translationProfileId,
    );
    const seconds = Math.max(5, Math.min(180, Number(profile?.timeout_seconds ?? 60)));
    return seconds * 1000 + 5000;
  }, [settings?.translationProfileId, translationProfilesQuery.data?.profiles]);

  useEffect(() => {
    translationBlocksRef.current = translationBlocks;
  }, [translationBlocks]);

  useEffect(() => {
    translationSkippedRef.current = translationSkipped;
  }, [translationSkipped]);

  useEffect(() => {
    if (settings && !settings.translationCacheEnabled) {
      translationCacheRef.current.clear();
    }
  }, [settings?.translationCacheEnabled]);

  useEffect(() => () => {
    if (wheelIdleTimerRef.current !== null) {
      window.clearTimeout(wheelIdleTimerRef.current);
    }
    translationAbortRef.current?.abort();
    translationPreloadAbortRef.current?.abort();
  }, []);

  const applySettings = useCallback((next: ReaderSettings) => {
    const view = viewRef.current;
    const renderer = view?.renderer;
    if (!renderer) return;

    const compactViewport = window.matchMedia('(max-width: 760px)').matches;
    const viewportWidth = hostRef.current?.clientWidth
      || window.visualViewport?.width
      || window.innerWidth;
    const effectiveMargin = compactViewport ? Math.min(next.margin, 12) : next.margin;
    const effectiveInlineSize = compactViewport
      ? Math.min(next.maxInlineSize, Math.max(1, viewportWidth))
      : next.maxInlineSize;

    renderer.setAttribute('flow', next.flow);
    renderer.setAttribute('margin', `${effectiveMargin}px`);
    const pageGapPercent = compactViewport
      ? 4
      : Math.max(3, Math.min(12, 3 + next.margin / 4));
    renderer.setAttribute('gap', `${pageGapPercent}%`);
    renderer.setAttribute('max-inline-size', `${effectiveInlineSize}px`);
    renderer.setAttribute('max-column-count', String(
      compactViewport || next.spread === 'nonespread' ? 1 : next.maxColumnCount,
    ));
    renderer.toggleAttribute('animated', next.animated && next.flow === 'paginated');
    renderer.setAttribute('background', THEME[next.theme].background);
    renderer.setStyles?.(readerCss(next, compactViewport));
  }, []);

  const updateSettings = useCallback((patch: Partial<ReaderSettings>) => {
    setSettings((current) => {
      if (!current) return current;
      const next = { ...current, ...patch };
      settingsRef.current = next;
      applySettings(next);
      saveSettings.mutate(patch);
      return next;
    });
  }, [applySettings, saveSettings]);

  const cacheTranslatedPage = useCallback((key: string, blocks: ReaderTranslationBlock[]) => {
    translationCacheRef.current.set(key, blocks);
    if (translationCacheRef.current.size > 100) {
      const oldest = translationCacheRef.current.keys().next().value as string | undefined;
      if (oldest) translationCacheRef.current.delete(oldest);
    }
  }, []);

  const cancelTranslationPreload = useCallback(() => {
    translationPreloadAbortRef.current?.abort();
    translationPreloadAbortRef.current = null;
    translationPreloadInFlightKeyRef.current = null;
    translationPreloadStartedAtRef.current = null;
    translationPreloadQueuedRef.current = null;
    translationPreloadTaskRef.current = null;
    setTranslationPreloading(false);
  }, []);

  const runTranslationPreload = useCallback((job: TranslationPreloadJob) => {
    const currentSettings = settingsRef.current;
    const enabled = !!currentSettings?.translationEnabled
      && currentSettings.translationView === 'translated'
      && currentSettings.translationCacheEnabled
      && currentSettings.translationPreloadNextPage
      && currentSettings.flow === 'paginated'
      && !!currentSettings.translationProfileId;
    const currentConfigMatches = !!currentSettings
      && currentSettings.translationProfileId === job.settings.translationProfileId
      && currentSettings.translationSourceLanguage === job.settings.translationSourceLanguage
      && currentSettings.translationTargetLanguage === job.settings.translationTargetLanguage
      && currentSettings.translationPrompt === job.settings.translationPrompt;
    if (!enabled || !currentConfigMatches || translationCacheRef.current.has(job.key)
        || translationInFlightKeyRef.current === job.key
        || translationPreloadInFlightKeyRef.current === job.key) return;

    if (translationPreloadInFlightKeyRef.current) {
      translationPreloadQueuedRef.current = job;
      return;
    }

    const controller = new AbortController();
    translationPreloadAbortRef.current = controller;
    translationPreloadInFlightKeyRef.current = job.key;
    translationPreloadStartedAtRef.current = Date.now();
    setTranslationPreloading(true);
    const configuredSource = normalizeLanguageCode(job.settings.translationSourceLanguage);
    const promise = translateReaderPage(id, {
      profile_id: job.settings.translationProfileId,
      format: fmt,
      source_language: configuredSource === 'auto' || !configuredSource
        ? bookLanguage || 'auto'
        : configuredSource,
      target_language: job.settings.translationTargetLanguage,
      prompt: job.settings.translationPrompt,
      cache_enabled: true,
      blocks: job.blocks,
    }, controller.signal);
    translationPreloadTaskRef.current = { key: job.key, controller, promise };
    void promise.then((response) => {
      if (!controller.signal.aborted && !response.skipped) {
        cacheTranslatedPage(job.key, response.blocks);
      }
    }).catch(() => {
      // Preloading is opportunistic. If the user opens this page while the
      // request is active, the foreground path joins the same promise and will
      // surface its provider error instead of starting a duplicate request.
    }).finally(() => {
      if (translationPreloadInFlightKeyRef.current === job.key) {
        translationPreloadInFlightKeyRef.current = null;
        translationPreloadStartedAtRef.current = null;
      }
      if (translationPreloadAbortRef.current === controller) {
        translationPreloadAbortRef.current = null;
      }
      if (translationPreloadTaskRef.current?.controller === controller) {
        translationPreloadTaskRef.current = null;
      }
      const queued = translationPreloadQueuedRef.current;
      translationPreloadQueuedRef.current = null;
      setTranslationPreloading(false);
      if (queued && queued.key !== job.key) {
        window.queueMicrotask(() => translationPreloadRunnerRef.current(queued));
      }
    });
  }, [bookLanguage, cacheTranslatedPage, fmt, id]);

  useEffect(() => {
    translationPreloadRunnerRef.current = runTranslationPreload;
  }, [runTranslationPreload]);

  const scheduleNextTranslationPreload = useCallback((activeSettings: ReaderSettings) => {
    if (!activeSettings.translationEnabled
        || activeSettings.translationView !== 'translated'
        || !activeSettings.translationCacheEnabled
        || !activeSettings.translationPreloadNextPage
        || activeSettings.flow !== 'paginated'
        || !activeSettings.translationProfileId) return;

    const extraction = extractVisiblePage(viewRef.current?.renderer, 1);
    const blocks = extraction?.blocks ?? [];
    if (!blocks.length || sourceAlreadyMatchesTarget(activeSettings, bookLanguage, blocks)) return;
    const key = translationPageKey(activeSettings, blocks);
    if (translationCacheRef.current.has(key)
        || translationInFlightKeyRef.current === key
        || translationPreloadInFlightKeyRef.current === key) return;
    translationPreloadRunnerRef.current({
      key,
      settings: { ...activeSettings },
      blocks,
    });
  }, [bookLanguage]);

  useEffect(() => {
    // Any translation configuration change invalidates an active speculative
    // request. The next completed current page will schedule a fresh preload.
    cancelTranslationPreload();
  }, [cancelTranslationPreload, settings?.flow, settings?.translationCacheEnabled, settings?.translationEnabled,
    settings?.translationPreloadNextPage, settings?.translationProfileId,
    settings?.translationPrompt, settings?.translationSourceLanguage,
    settings?.translationTargetLanguage, settings?.translationView]);

  useEffect(() => {
    if (!translationLoading && !translationPreloading) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      const foregroundStarted = translationStartedAtRef.current;
      if (translationLoading
          && !translationInFlightKeyRef.current
          && !translationTransitionRef.current) {
        translationStartedAtRef.current = null;
        setTranslationLoading(false);
      } else if (translationLoading && foregroundStarted !== null
          && now - foregroundStarted > translationRequestTimeoutMs) {
        const failedKey = translationInFlightKeyRef.current;
        const message = t('Page translation timed out.');
        translationAbortRef.current?.abort();
        translationAbortRef.current = null;
        translationInFlightKeyRef.current = null;
        translationStartedAtRef.current = null;
        if (failedKey) translationFailureRef.current = { key: failedKey, message };
        cancelTranslationPreload();
        setTranslationLoading(false);
        setTranslationError(message);
      }

      const preloadStarted = translationPreloadStartedAtRef.current;
      if (translationPreloading && !translationPreloadInFlightKeyRef.current) {
        translationPreloadStartedAtRef.current = null;
        setTranslationPreloading(false);
      } else if (translationPreloading && preloadStarted !== null
          && now - preloadStarted > translationRequestTimeoutMs) {
        cancelTranslationPreload();
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cancelTranslationPreload, t, translationLoading, translationPreloading,
    translationRequestTimeoutMs]);

  useEffect(() => {
    if (!settings) return;
    let frame = 0;
    const reflow = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => applySettings(settings));
    };
    window.addEventListener('resize', reflow);
    window.visualViewport?.addEventListener('resize', reflow);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', reflow);
      window.visualViewport?.removeEventListener('resize', reflow);
    };
  }, [applySettings, settings]);


  const dismissSelection = useCallback(() => {
    pendingSelectionRangeRef.current = null;
    setPendingSelection(null);
    setSelectionTranslationError(null);
    viewRef.current?.deselect();
  }, []);

  const restoreInlineTranslations = useCallback(() => {
    const patches = inlineTranslationPatchesRef.current.splice(0).reverse();
    for (const patch of patches) {
      if (patch.marker.isConnected) patch.marker.replaceWith(patch.original);
    }
  }, []);

  const navigate = useCallback(async (action: 'prev' | 'next' | 'left' | 'right') => {
    restoreInlineTranslations();
    dismissSelection();
    const view = viewRef.current;
    if (!view) return;
    if (action === 'prev') await view.prev();
    else if (action === 'next') await view.next();
    else if (action === 'left') await view.goLeft();
    else await view.goRight();
  }, [dismissSelection, restoreInlineTranslations]);

  const showTranslationPage = useCallback((index: number) => {
    const bounded = Math.max(0, Math.min(translationPageCountRef.current - 1, index));
    translationPageIndexRef.current = bounded;
    setTranslationPageIndex(bounded);
  }, []);

  const navigateTranslation = useCallback((direction: 'prev' | 'next'): boolean => {
    const currentSettings = settingsRef.current;
    const active = !!currentSettings?.translationEnabled
      && currentSettings.translationView === 'translated'
      && translationBlocksRef.current.length > 0
      && !translationSkippedRef.current;
    if (!active) return false;
    if (translationTransitionRef.current) return true;

    const current = translationPageIndexRef.current;
    const count = translationPageCountRef.current;
    if (direction === 'next' && current < count - 1) {
      showTranslationPage(current + 1);
      return true;
    }
    if (direction === 'prev' && current > 0) {
      showTranslationPage(current - 1);
      return true;
    }

    translationLandingRef.current = direction === 'next' ? 'first' : 'last';
    translationTransitionRef.current = true;
    setTranslationLoading(true);
    void navigate(direction).catch((cause) => {
      translationTransitionRef.current = false;
      translationLandingRef.current = null;
      setTranslationLoading(false);
      setTranslationError(cause instanceof Error ? cause.message : t('Page translation failed.'));
    });
    return true;
  }, [navigate, showTranslationPage, t]);

  const navigateReader = useCallback((action: 'prev' | 'next' | 'left' | 'right') => {
    const direction = action === 'prev' || action === 'left' ? 'prev' : 'next';
    if (navigateTranslation(direction)) return;
    void navigate(action);
  }, [navigate, navigateTranslation]);

  const handleReaderWheel = useCallback((event: WheelEvent) => {
    if (settingsRef.current?.flow !== 'paginated') return;
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    if (!allowsWheelPageTurn(event.target) || event.deltaY === 0) return;

    event.preventDefault();
    const now = performance.now();
    if (now < wheelLockUntilRef.current) return;

    const scale = event.deltaMode === WheelEvent.DOM_DELTA_LINE
      ? 16
      : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
        ? Math.max(1, window.innerHeight)
        : 1;
    const delta = event.deltaY * scale;
    const direction = Math.sign(delta);
    if (direction !== wheelDirectionRef.current) {
      wheelDeltaRef.current = 0;
      wheelDirectionRef.current = direction;
    }
    wheelDeltaRef.current += delta;

    if (wheelIdleTimerRef.current !== null) {
      window.clearTimeout(wheelIdleTimerRef.current);
    }
    wheelIdleTimerRef.current = window.setTimeout(() => {
      wheelDeltaRef.current = 0;
      wheelDirectionRef.current = 0;
      wheelIdleTimerRef.current = null;
    }, READER_WHEEL_IDLE_RESET_MS);

    if (Math.abs(wheelDeltaRef.current) < READER_WHEEL_THRESHOLD_PX) return;
    const action = wheelDeltaRef.current > 0 ? 'next' : 'prev';
    wheelDeltaRef.current = 0;
    wheelDirectionRef.current = 0;
    wheelLockUntilRef.current = now + READER_WHEEL_COOLDOWN_MS;
    navigateReader(action);
  }, [navigateReader]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    stage.addEventListener('wheel', handleReaderWheel, { passive: false });
    return () => stage.removeEventListener('wheel', handleReaderWheel);
  }, [handleReaderWheel]);

  useEffect(() => {
    if (!ready || !settings?.translationEnabled
        || settings.translationView !== 'translated'
        || !settings.translationProfileId) {
      cancelTranslationPreload();
      translationAbortRef.current?.abort();
      translationAbortRef.current = null;
      translationInFlightKeyRef.current = null;
      translationCurrentKeyRef.current = null;
      translationTransitionRef.current = false;
      translationLandingRef.current = null;
      setTranslationLoading(false);
      setTranslationError(null);
      setTranslationSkipped(false);
      setTranslationBlocks([]);
      setTranslationSegments([]);
      setTranslationLayout(null);
      showTranslationPage(0);
      return;
    }

    if (sourceAlreadyMatchesTarget(settings, bookLanguage)) {
      cancelTranslationPreload();
      translationAbortRef.current?.abort();
      translationAbortRef.current = null;
      translationInFlightKeyRef.current = null;
      translationTransitionRef.current = false;
      translationLandingRef.current = null;
      setTranslationBlocks([]);
      setTranslationSegments([]);
      setTranslationLayout(null);
      showTranslationPage(0);
      setTranslationLoading(false);
      setTranslationError(null);
      setTranslationSkipped(true);
      return;
    }

    const timer = window.setTimeout(async () => {
      const extraction = extractVisiblePage(viewRef.current?.renderer);
      if (!extraction) {
        if (!translationTransitionRef.current) {
          setTranslationBlocks([]);
          setTranslationSegments([]);
          setTranslationLayout(null);
        }
        setTranslationLoading(false);
        setTranslationSkipped(false);
        setTranslationError(t('No visible text was found on this page.'));
        return;
      }
      const { blocks, styles: sourceStyles, sourceRuns, segments, layout } = extraction;
      if (!blocks.length) {
        translationAbortRef.current?.abort();
        translationAbortRef.current = null;
        translationInFlightKeyRef.current = null;
        setTranslationBlocks([]);
        setTranslationSegments(segments);
        setTranslationLayout(layout);
        setTranslationLoading(false);
        setTranslationError(null);
        setTranslationSkipped(false);
        translationTransitionRef.current = false;
        scheduleNextTranslationPreload(settings);
        return;
      }
      if (sourceAlreadyMatchesTarget(settings, bookLanguage, blocks)) {
        cancelTranslationPreload();
        translationAbortRef.current?.abort();
        translationAbortRef.current = null;
        translationInFlightKeyRef.current = null;
        translationTransitionRef.current = false;
        translationLandingRef.current = null;
        setTranslationBlocks([]);
        setTranslationSegments([]);
        setTranslationLayout(null);
        setTranslationLoading(false);
        setTranslationError(null);
        setTranslationSkipped(true);
        return;
      }

      const key = translationPageKey(settings, blocks);
      translationCurrentKeyRef.current = key;
      const previousFailure = translationFailureRef.current;
      if (previousFailure?.key === key) {
        translationTransitionRef.current = false;
        translationLandingRef.current = null;
        setTranslationBlocks([]);
        setTranslationSegments([]);
        setTranslationLayout(null);
        setTranslationLoading(false);
        setTranslationError(previousFailure.message);
        return;
      }
      const styled = (translated: ReaderTranslationBlock[]): StyledTranslationBlock[] =>
        translated.map((block) => ({
          ...block,
          style: sourceStyles[block.id],
          sourceRuns: sourceRuns[block.id],
        }));
      const applyTranslationResponse = (response: ReaderTranslationResponse) => {
        if (translationFailureRef.current?.key === key) translationFailureRef.current = null;
        setTranslationError(null);
        if (response.skipped) {
          translationTransitionRef.current = false;
          translationLandingRef.current = null;
          setTranslationSkipped(true);
          setTranslationBlocks([]);
          setTranslationSegments([]);
          setTranslationLayout(null);
          return;
        }
        if (settings.translationCacheEnabled) {
          cacheTranslatedPage(key, response.blocks);
        }
        setTranslationLayout(layout);
        setTranslationSegments(segments);
        setTranslationBlocks(styled(response.blocks));
        translationTransitionRef.current = false;
        scheduleNextTranslationPreload(settings);
      };
      setTranslationSkipped(false);
      const local = settings.translationCacheEnabled
        ? translationCacheRef.current.get(key)
        : undefined;
      if (local) {
        setTranslationLayout(layout);
        setTranslationSegments(segments);
        setTranslationBlocks(styled(local));
        setTranslationLoading(false);
        setTranslationError(null);
        translationTransitionRef.current = false;
        scheduleNextTranslationPreload(settings);
        return;
      }
      if (translationInFlightKeyRef.current === key) return;

      const preloadTask = translationPreloadTaskRef.current;
      if (preloadTask && preloadTask.key !== key) {
        cancelTranslationPreload();
      }
      if (preloadTask?.key === key) {
        translationInFlightKeyRef.current = key;
        translationStartedAtRef.current = translationPreloadStartedAtRef.current ?? Date.now();
        if (!translationTransitionRef.current) {
          setTranslationBlocks([]);
          setTranslationSegments([]);
          setTranslationLayout(layout);
        }
        setTranslationLoading(true);
        setTranslationError(null);
        try {
          const response = await preloadTask.promise;
          if (!preloadTask.controller.signal.aborted) applyTranslationResponse(response);
        } catch (cause) {
          if (!preloadTask.controller.signal.aborted) {
            const message = cause instanceof Error ? cause.message : t('Page translation failed.');
            translationFailureRef.current = { key, message };
            cancelTranslationPreload();
            setTranslationError(message);
          }
        } finally {
          if (translationInFlightKeyRef.current === key) {
            translationInFlightKeyRef.current = null;
            translationStartedAtRef.current = null;
            setTranslationLoading(false);
          }
        }
        return;
      }

      translationAbortRef.current?.abort();
      const controller = new AbortController();
      translationAbortRef.current = controller;
      translationInFlightKeyRef.current = key;
      translationStartedAtRef.current = Date.now();
      if (!translationTransitionRef.current) {
        setTranslationBlocks([]);
        setTranslationSegments([]);
        setTranslationLayout(layout);
      }
      setTranslationLoading(true);
      setTranslationError(null);
      try {
        const configuredSource = normalizeLanguageCode(settings.translationSourceLanguage);
        const response = await translateReaderPage(id, {
          profile_id: settings.translationProfileId,
          format: fmt,
          source_language: configuredSource === 'auto' || !configuredSource
            ? bookLanguage || 'auto'
            : configuredSource,
          target_language: settings.translationTargetLanguage,
          prompt: settings.translationPrompt,
          cache_enabled: settings.translationCacheEnabled,
          blocks,
        }, controller.signal);
        if (controller.signal.aborted) return;
        applyTranslationResponse(response);
      } catch (cause) {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : t('Page translation failed.');
        translationFailureRef.current = { key, message };
        cancelTranslationPreload();
        setTranslationError(message);
      } finally {
        if (translationInFlightKeyRef.current === key) {
          translationInFlightKeyRef.current = null;
          translationStartedAtRef.current = null;
        }
        if (translationAbortRef.current === controller) translationAbortRef.current = null;
        if (!controller.signal.aborted) setTranslationLoading(false);
      }
    }, TRANSLATION_DEBOUNCE_MS);

    // Relocate can fire several times while Foliate settles the same page.
    // Cancel only the pending debounce here. An in-flight request is kept until
    // the next stable page signature is known; identical signatures are deduped.
    return () => window.clearTimeout(timer);
  }, [bookLanguage, fmt, id, location.cfi, location.fraction, ready,
    settings?.translationEnabled, settings?.translationProfileId,
    settings?.translationCacheEnabled, settings?.translationPreloadNextPage,
    settings?.translationPrompt, settings?.translationSourceLanguage,
    settings?.translationTargetLanguage, settings?.translationView,
    cacheTranslatedPage, cancelTranslationPreload, scheduleNextTranslationPreload,
    showTranslationPage, t, translationRetry]);

  useLayoutEffect(() => {
    const content = translationPagerContentRef.current;
    if (!content || !translationLayout || !translationSegments.length) {
      translationPageCountRef.current = 1;
      showTranslationPage(0);
      return;
    }

    let frame = 0;
    const measure = () => {
      const step = Math.max(1, translationLayout.contentWidth + translationLayout.columnGap);
      const count = Math.max(1, Math.ceil(
        (content.scrollWidth + translationLayout.columnGap - 0.5) / step,
      ));
      translationPageCountRef.current = count;
      const landing = translationLandingRef.current;
      const nextIndex = landing === 'last'
        ? count - 1
        : landing === 'first'
          ? 0
          : Math.min(translationPageIndexRef.current, count - 1);
      translationLandingRef.current = null;
      showTranslationPage(nextIndex);
    };
    const schedule = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(measure);
    };
    schedule();
    void document.fonts?.ready?.then(schedule);
    const observer = new ResizeObserver(schedule);
    observer.observe(content);
    if (stageRef.current) observer.observe(stageRef.current);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [showTranslationPage, translationBlocks, translationLayout, translationSegments]);

  useEffect(() => {
    if (!selectedFormat || !settingsQuery.data || !positionQuery.isFetched || !hostRef.current) return;
    let cancelled = false;
    const host = hostRef.current;
    const view = document.createElement('foliate-view') as FoliateView;
    view.className = styles.foliateView;
    viewRef.current = view;
    host.replaceChildren(view);
    setReady(false);
    setError(null);
    setBookLanguage('');
    const initialSettings = settingsQuery.data.reader;
    settingsRef.current = initialSettings;
    setSettings(initialSettings);

    const onRelocate = (event: Event) => {
      const detail = (event as CustomEvent<FoliateLocation>).detail;
      if (inlineTranslationPatchesRef.current.length) {
        const previous = currentRef.current;
        const sameLogicalPage = (
          detail.location?.current && previous.location?.current
            ? detail.location.current === previous.location.current
            : Math.abs((detail.fraction ?? 0) - (previous.fraction ?? 0)) < 0.0005
        );
        // Ignore only reflow caused by the temporary replacement. Native swipe
        // or another Foliate navigation path still restores the original DOM.
        if (sameLogicalPage) return;
        restoreInlineTranslations();
      }
      dismissSelection();
      currentRef.current = detail;
      setLocation(detail);
      if (detail.cfi) schedulePosition(detail.cfi, detail.fraction ?? 0);
    };

    const attachSelection = (doc: Document, index: number) => {
      const readSelection = () => {
        const selection = doc.getSelection();
        if (!selection || selection.isCollapsed || !selection.rangeCount) {
          dismissSelection();
          return;
        }
        const range = selection.getRangeAt(0).cloneRange();
        const rawText = selection.toString();
        const text = rawText.replace(/\s+/g, ' ').trim();
        if (!text) return;
        pendingSelectionRangeRef.current = { range, doc };
        setSelectionTranslationError(null);
        setPendingSelection({
          value: view.getCFI(index, range),
          text,
          leadingWhitespace: rawText.match(/^\s+/u)?.[0] ?? '',
          trailingWhitespace: rawText.match(/\s+$/u)?.[0] ?? '',
        });
      };
      doc.addEventListener('mouseup', readSelection);
      doc.addEventListener('keyup', readSelection);
      doc.addEventListener('keydown', onReaderKeyDown);
      doc.addEventListener('wheel', handleReaderWheel, { passive: false });
    };
    const onLoad = (event: Event) => {
      const detail = (event as CustomEvent<{ doc: Document; index: number }>).detail;
      setBookLanguage((current) => current || normalizeLanguageCode(detail.doc.documentElement.lang));
      attachSelection(detail.doc, detail.index);
    };
    const onDrawAnnotation = (event: Event) => {
      const { draw, annotation } = (event as CustomEvent<{
        draw: (fn: unknown, options: unknown) => void;
        annotation: FoliateAnnotation;
      }>).detail;
      draw(Overlayer.highlight, { color: annotation.color ?? 'yellow' });
    };
    const onShowAnnotation = (event: Event) => {
      const value = (event as CustomEvent<{ value: string }>).detail.value;
      setSelectedAnnotation(annotationsRef.current.get(value) ?? null);
      setPanel('notes');
    };
    const redrawAnnotations = () => {
      for (const annotation of annotationsRef.current.values()) {
        void view.addAnnotation(annotation);
      }
    };

    view.addEventListener('relocate', onRelocate);
    view.addEventListener('load', onLoad);
    view.addEventListener('draw-annotation', onDrawAnnotation);
    view.addEventListener('show-annotation', onShowAnnotation);
    view.addEventListener('create-overlay', redrawAnnotations);

    const open = async () => {
      try {
        const formatName = selectedFormat.format.toUpperCase();
        const response = await fetch(resourceUrl(`/show/${id}/${formatName.toLowerCase()}`), {
          credentials: 'include',
        });
        if (!response.ok) throw new Error(t('Could not load the book file ({status})', { status: response.status }));
        const data = await response.arrayBuffer();
        if (cancelled) return;
        await view.open(new File([data], fileName(formatName), { type: MIME[formatName] ?? '' }));
        if (cancelled) return;
        applySettings(initialSettings);
        setBookLanguage((current) => normalizeLanguageCode(view.book?.metadata?.language) || current);
        setTitle(formatLanguageMap(view.book?.metadata?.title) || bookQuery.data?.title || t('Untitled'));
        setToc(flattenToc(view.book?.toc ?? []));
        setSectionFractions(view.getSectionFractions());
        const annotationPayload = await apiGet<{ annotations: ServerAnnotation[] }>(
          `/annotations/${id}/data.json?format=${encodeURIComponent(fmt)}`,
        ).catch(() => ({ annotations: [] }));
        const loaded = annotationPayload.annotations
          .filter((row) => !!row.cfi_range)
          .map((row): FoliateAnnotation => ({
            value: row.cfi_range ?? '',
            color: row.highlight_color || 'yellow',
            note: row.note_text,
            id: row.annotation_id,
            text: row.highlighted_text,
          }));
        annotationsRef.current = new Map(loaded.map((item) => [item.value, item]));
        setAnnotations(loaded);

        const savedLocator = positionQuery.data?.bookmark;
        const legacyFb2Fraction = fmt === 'fb2' ? parseFb2ScrollBookmark(savedLocator) : null;
        const savedFraction = Number(positionQuery.data?.position_fraction ?? legacyFb2Fraction ?? 0);
        const lastLocation = savedFraction > 0
          ? { fraction: Math.min(1, Math.max(0, savedFraction)) }
          : savedLocator || undefined;
        await view.init({ lastLocation, showTextStart: true });
        if (cancelled) return;
        redrawAnnotations();
        setReady(true);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : t('Could not open this book.'));
      }
    };
    void open();

    return () => {
      cancelled = true;
      restoreInlineTranslations();
      window.speechSynthesis?.cancel();
      view.removeEventListener('relocate', onRelocate);
      view.removeEventListener('load', onLoad);
      view.removeEventListener('draw-annotation', onDrawAnnotation);
      view.removeEventListener('show-annotation', onShowAnnotation);
      view.removeEventListener('create-overlay', redrawAnnotations);
      view.close();
      view.remove();
      if (viewRef.current === view) viewRef.current = null;
    };
  }, [applySettings, bookQuery.data?.title, fmt, id, positionQuery.data?.bookmark,
    positionQuery.data?.position_fraction, positionQuery.isFetched, selectedFormat,
    settingsQuery.data, schedulePosition, dismissSelection, handleReaderWheel,
    restoreInlineTranslations, t]);
  function onReaderKeyDown(event: KeyboardEvent) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    if (isReaderTypingTarget(event.target)) return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      navigateReader('left');
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      navigateReader('right');
    } else if (!event.repeat && (event.key === '+' || event.code === 'NumpadAdd'
        || (event.code === 'Equal' && event.shiftKey))) {
      event.preventDefault();
      const currentSettings = settingsRef.current;
      if (currentSettings) {
        updateSettings({ fontSize: Math.min(FONT_MAX, currentSettings.fontSize + 1) });
      }
    } else if (!event.repeat && (event.key === '-' || event.code === 'NumpadSubtract')) {
      event.preventDefault();
      const currentSettings = settingsRef.current;
      if (currentSettings) {
        updateSettings({ fontSize: Math.max(FONT_MIN, currentSettings.fontSize - 1) });
      }
    } else if (!event.repeat && event.key.toLowerCase() === 't') {
      event.preventDefault();
      const currentSettings = settingsRef.current;
      if (!currentSettings?.translationProfileId) {
        setPanel('translation');
      } else if (currentSettings.translationView === 'translated') {
        updateSettings({ translationView: 'original' });
      } else {
        updateSettings({ translationEnabled: true, translationView: 'translated' });
      }
    } else if (event.key === 'Escape') {
      setPanel(null);
      dismissSelection();
    }
  }

  useEffect(() => {
    document.addEventListener('keydown', onReaderKeyDown);
    return () => document.removeEventListener('keydown', onReaderKeyDown);
  });

  const runSearch = async () => {
    const query = searchText.trim();
    const view = viewRef.current;
    if (!query || !view) return;
    const run = ++searchRunRef.current;
    setSearching(true);
    setSearchProgress(0);
    setSearchResults([]);
    const found: SearchResult[] = [];
    try {
      for await (const raw of view.search({ query, matchCase: false, matchDiacritics: false })) {
        if (run !== searchRunRef.current) return;
        if (raw === 'done') break;
        const item = raw as { progress?: number; label?: string; subitems?: Array<{ cfi: string; excerpt: SearchExcerpt }> };
        if (typeof item.progress === 'number') setSearchProgress(item.progress);
        for (const subitem of item.subitems ?? []) {
          found.push({ cfi: subitem.cfi, label: item.label ?? '', excerpt: subitem.excerpt });
        }
        setSearchResults([...found]);
      }
    } finally {
      if (run === searchRunRef.current) setSearching(false);
    }
  };
  const createHighlight = async (withNote = false) => {
    const selection = pendingSelection;
    const view = viewRef.current;
    if (!selection || !view) return;
    const note = withNote ? window.prompt(t('Note'), '') : null;
    if (withNote && note === null) return;
    const row = await apiPost<ServerAnnotation>(`/annotations/${id}`, {
      cfi_range: selection.value,
      highlighted_text: selection.text,
      highlight_color: 'yellow',
      note_text: note || null,
      format: fmt.toUpperCase(),
    });
    const annotation: FoliateAnnotation = {
      value: row.cfi_range ?? selection.value,
      color: row.highlight_color || 'yellow',
      note: row.note_text,
      id: row.annotation_id,
      text: row.highlighted_text,
    };
    annotationsRef.current.set(annotation.value, annotation);
    setAnnotations(Array.from(annotationsRef.current.values()));
    await view.addAnnotation(annotation);
    view.deselect();
    setPendingSelection(null);
  };

  const openSelectedTextInChatGpt = () => {
    const text = pendingSelection?.text.trim();
    if (!text) return;
    const opened = window.open(chatGptSelectedTextUrl(text), '_blank', 'noopener,noreferrer');
    if (opened) opened.opener = null;
  };

  const translateSelectedText = async () => {
    const selection = pendingSelection;
    const source = pendingSelectionRangeRef.current;
    const currentSettings = settingsRef.current;
    if (!selection || !source || selectionTranslationLoading) return;
    if (!currentSettings?.translationProfileId) {
      setPanel('translation');
      return;
    }
    if (!source.range.commonAncestorContainer.isConnected) {
      dismissSelection();
      return;
    }

    setSelectionTranslationLoading(true);
    setSelectionTranslationError(null);
    try {
      const configuredSource = normalizeLanguageCode(currentSettings.translationSourceLanguage);
      const response = await translateReaderPage(id, {
        profile_id: currentSettings.translationProfileId,
        format: fmt,
        source_language: configuredSource === 'auto' || !configuredSource
          ? bookLanguage || 'auto'
          : configuredSource,
        target_language: currentSettings.translationTargetLanguage,
        prompt: currentSettings.translationPrompt,
        cache_enabled: false,
        blocks: [{ id: 'selection', tag: 'span', text: selection.text }],
      });
      if (response.skipped) {
        setSelectionTranslationError(t('The selected text is already in the target language.'));
        return;
      }
      const translated = response.blocks.find((block) => block.id === 'selection')?.text.trim();
      if (!translated) throw new Error(t('Page translation failed.'));

      const { range, doc } = source;
      const original = range.extractContents();
      const marker = doc.createElement('span');
      marker.setAttribute('data-reader-inline-translation', 'true');
      marker.lang = normalizeLanguageCode(currentSettings.translationTargetLanguage);
      marker.title = selection.text;
      marker.textContent = `${selection.leadingWhitespace}${translated}${selection.trailingWhitespace}`;
      marker.style.font = 'inherit';
      marker.style.color = 'inherit';
      marker.style.background = 'transparent';
      marker.style.borderBottom = '1px dotted currentColor';
      marker.style.boxDecorationBreak = 'clone';
      range.insertNode(marker);
      inlineTranslationPatchesRef.current.push({ marker, original });
      doc.getSelection()?.removeAllRanges();
      pendingSelectionRangeRef.current = null;
      setPendingSelection(null);
    } catch (cause) {
      setSelectionTranslationError(cause instanceof Error ? cause.message : t('Page translation failed.'));
    } finally {
      setSelectionTranslationLoading(false);
    }
  };

  const removeAnnotation = async (annotation: FoliateAnnotation) => {
    if (!annotation.id) return;
    await apiDelete(`/annotations/${id}/${encodeURIComponent(annotation.id)}?format=${encodeURIComponent(fmt)}`);
    annotationsRef.current.delete(annotation.value);
    setAnnotations(Array.from(annotationsRef.current.values()));
    await viewRef.current?.deleteAnnotation(annotation);
    if (selectedAnnotation?.id === annotation.id) setSelectedAnnotation(null);
  };

  const updateAnnotationNote = async (annotation: FoliateAnnotation) => {
    if (!annotation.id) return;
    const note = window.prompt(t('Note'), annotation.note ?? '');
    if (note === null) return;
    const row = await apiPatch<ServerAnnotation>(
      `/annotations/${id}/${encodeURIComponent(annotation.id)}`,
      { note_text: note || null, format: fmt.toUpperCase() },
    );
    const updated = { ...annotation, note: row.note_text };
    annotationsRef.current.set(updated.value, updated);
    setAnnotations(Array.from(annotationsRef.current.values()));
    setSelectedAnnotation(updated);
  };
  const addReaderBookmark = () => {
    const locator = currentRef.current.cfi;
    if (!locator) return;
    createBookmark.mutate({
      locator,
      progression: currentRef.current.fraction ?? 0,
      label: currentRef.current.pageItem?.label || undefined,
      chapter: currentRef.current.tocItem?.label || undefined,
    });
  };

  const openReaderBookmark = (bookmark: ReaderBookmark) => {
    restoreInlineTranslations();
    void viewRef.current?.goTo(bookmark.locator);
    setPanel(null);
  };

  const toggleSpeech = () => {
    const synth = window.speechSynthesis;
    const contents = viewRef.current?.renderer?.getContents?.();
    if (!synth || !contents?.length) return;
    if (synth.speaking) {
      synth.cancel();
      setSpeaking(false);
      return;
    }
    const doc = contents[0].doc;
    const text = (doc.body?.innerText ?? '').replace(/\s+/g, ' ').trim();
    if (!text) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = doc.documentElement.lang || navigator.language;
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    setSpeaking(true);
    synth.speak(utterance);
  };

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void hostRef.current?.parentElement?.requestFullscreen();
  };

  const progress = Math.max(0, Math.min(1, location.fraction ?? 0));
  const percent = Math.round(progress * 100);
  const translationRequested = !!settings?.translationEnabled
    && settings.translationView === 'translated'
    && !!settings.translationProfileId;
  const translationOverlayVisible = translationRequested
    && !translationSkipped && !!translationLayout && translationSegments.length > 0;
  const translationActivity = translationRequested && !translationError
    && (translationLoading || (translationPreloading && translationOverlayVisible));
  const translatedBlockById = new Map(translationBlocks.map((block) => [block.id, block]));
  const bookmarks = bookmarksQuery.data?.bookmarks ?? [];
  const loading = bookQuery.isLoading || settingsQuery.isLoading || positionQuery.isLoading;

  if (loading) return <SpinnerCentered size={44} />;
  if (bookQuery.error) return <EmptyState message={t('Could not load the book.')} />;
  if (!selectedFormat) return <EmptyState message={t('No supported reader format is available.')} />;
  return (
    <main className={`${styles.reader} ${styles[settings?.theme ?? 'lightTheme']}`}>
      <header className={styles.topBar}>
        <Link href={`/book/${id}`} className={styles.iconButton} title={t('Close reader')}>
          <X size={20} aria-hidden="true" />
        </Link>
        <div className={styles.bookIdentity}>
          <strong>{title || bookQuery.data?.title}</strong>
          <span>{selectedFormat.format.toUpperCase()}</span>
        </div>
        <nav className={styles.toolbar} aria-label={t('Reader tools')}>
          <button className={styles.iconButton} onClick={() => setPanel(panel === 'toc' ? null : 'toc')}
            title={t('Table of contents')} aria-pressed={panel === 'toc'}>
            <List size={19} aria-hidden="true" />
          </button>
          <button className={styles.iconButton} onClick={() => setPanel(panel === 'search' ? null : 'search')}
            title={t('Search in book')} aria-pressed={panel === 'search'}>
            <Search size={19} aria-hidden="true" />
          </button>
          <button className={styles.iconButton} onClick={addReaderBookmark}
            title={t('Add bookmark')} disabled={!location.cfi || createBookmark.isPending}>
            <Bookmark size={19} aria-hidden="true" />
          </button>
          <button className={styles.iconButton} onClick={() => setPanel(panel === 'bookmarks' ? null : 'bookmarks')}
            title={t('Bookmarks')} aria-pressed={panel === 'bookmarks'}>
            <BookOpen size={19} aria-hidden="true" />
          </button>
          <button className={styles.iconButton} onClick={() => setPanel(panel === 'notes' ? null : 'notes')}
            title={t('Highlights and notes')} aria-pressed={panel === 'notes'}>
            <StickyNote size={19} aria-hidden="true" />
          </button>
          <button className={styles.iconButton} onClick={toggleSpeech}
            title={speaking ? t('Stop reading aloud') : t('Read aloud')} aria-pressed={speaking}>
            {speaking ? <Square size={18} aria-hidden="true" /> : <Volume2 size={19} aria-hidden="true" />}
          </button>
          {settings?.translationProfileId && (
            <div className={styles.translationToggle} role="group" aria-label={t('Page language view')}>
              <button type="button" className={settings.translationView === 'original' || translationSkipped ? styles.translationToggleActive : ''}
                onClick={() => updateSettings({ translationView: 'original' })}
                aria-pressed={settings.translationView === 'original' || translationSkipped}
                aria-label={t('Original')} title={`${t('Original')} (T)`}>
                <BookOpen size={16} aria-hidden="true" />
              </button>
              <button type="button" className={settings.translationView === 'translated' && !translationSkipped ? styles.translationToggleActive : ''}
                onClick={() => updateSettings({ translationEnabled: true, translationView: 'translated' })}
                aria-pressed={settings.translationView === 'translated' && !translationSkipped}
                aria-busy={translationActivity}
                data-translation-activity={translationLoading
                  ? 'translation'
                  : translationPreloading && translationOverlayVisible
                    ? 'preload'
                    : 'idle'}
                aria-label={t('Translation')} title={`${t('Translation')} (T)`}>
                <span className={`${styles.translationToggleIcon} ${
                  translationActivity ? styles.translationToggleIconBusy : ''
                }`}>
                  <Languages size={16} aria-hidden="true" />
                </span>
              </button>
            </div>
          )}
          <button className={styles.iconButton}
            onClick={() => setPanel(panel === 'translation' ? null : 'translation')}
            title={t('Page translation')} aria-pressed={panel === 'translation'}>
            <Languages size={19} aria-hidden="true" />
          </button>
          <button className={styles.iconButton} onClick={() => setPanel(panel === 'settings' ? null : 'settings')}
            title={t('Reader settings')} aria-pressed={panel === 'settings'}>
            <Settings size={19} aria-hidden="true" />
          </button>
          <button className={styles.iconButton} onClick={toggleFullscreen} title={t('Full screen')}>
            <Maximize size={19} aria-hidden="true" />
          </button>
        </nav>
      </header>

      {pendingSelection && (
        <div className={styles.selectionBar} role="toolbar" aria-label={t('Selected text actions')}>
          <span>{pendingSelection.text.slice(0, 120)}</span>
          <button onClick={() => void createHighlight(false)}>
            <Highlighter size={17} aria-hidden="true" /> {t('Highlight')}
          </button>
          <button onClick={() => void createHighlight(true)}>
            <StickyNote size={17} aria-hidden="true" /> {t('Add note')}
          </button>
          <button onClick={() => void translateSelectedText()}
            disabled={selectionTranslationLoading}>
            <Languages size={17} aria-hidden="true" /> {t('Translation')}
          </button>
          <button onClick={openSelectedTextInChatGpt}
            title={t('Open selected text in ChatGPT')}
            aria-label={t('Open selected text in ChatGPT')}>
            <FontAwesomeIcon icon={faOpenai} className={styles.selectionChatGptIcon} aria-hidden="true" />
            ChatGPT
          </button>
          <button onClick={dismissSelection}>
            <X size={17} aria-hidden="true" /> {t('Cancel')}
          </button>
          {selectionTranslationError && (
            <span className={styles.selectionError} role="alert">{selectionTranslationError}</span>
          )}
        </div>
      )}

      <div className={styles.workspace}>
        {panel && <ReaderSidePanel
          panel={panel} onClose={() => setPanel(null)} toc={toc}
          onNavigate={(target) => { restoreInlineTranslations(); dismissSelection(); void viewRef.current?.goTo(target); setPanel(null); }}
          searchText={searchText} setSearchText={setSearchText} runSearch={() => void runSearch()}
          searching={searching} searchProgress={searchProgress} searchResults={searchResults}
          bookmarks={bookmarks} openBookmark={openReaderBookmark}
          deleteBookmark={(bookmarkId) => deleteBookmark.mutate(bookmarkId)}
          annotations={annotations}
          showAnnotation={(annotation) => { restoreInlineTranslations(); void viewRef.current?.showAnnotation(annotation); setPanel(null); }}
          removeAnnotation={(annotation) => void removeAnnotation(annotation)}
          updateAnnotationNote={(annotation) => void updateAnnotationNote(annotation)}
          settings={settings} updateSettings={updateSettings}
        />}
        <section
          ref={stageRef}
          className={styles.stage}
          aria-label={t('Book content')}
        >
          {!ready && !error && <div className={styles.stageLoading}><SpinnerCentered size={44} /></div>}
          {error && <EmptyState message={error} />}
          <div ref={hostRef} className={styles.host} data-ready={ready ? 'true' : 'false'} />
          {translationRequested && (translationError || translationSkipped) && (
            <div className={styles.translationStatus} role={translationError ? 'alert' : 'status'}>
              {translationSkipped && (
                <span>{t('The book is already in the target language.')}</span>
              )}
              {translationError && (
                <>
                  <span>{translationError}</span>
                  <button type="button" onClick={() => {
                    if (translationFailureRef.current?.key === translationCurrentKeyRef.current) {
                      translationFailureRef.current = null;
                    }
                    setTranslationError(null);
                    setTranslationRetry((value) => value + 1);
                  }}>
                    {t('Retry')}
                  </button>
                </>
              )}
            </div>
          )}
          {translationOverlayVisible && settings && translationLayout && (
            <div
              className={styles.translationOverlay}
              data-reader-translation-overlay
              aria-live="polite"
              style={{
                background: THEME[settings.theme].background,
                color: THEME[settings.theme].text,
              }}
            >
              <div
                className={styles.translationPageShell}
                style={{
                  width: `${translationLayout.pageWidth}px`,
                  height: `${translationLayout.pageHeight}px`,
                }}
              >
                <div className={styles.translationPagerViewport}>
                  <div
                    ref={translationPagerContentRef}
                    className={styles.translationPagerContent}
                    style={{
                      left: `${translationLayout.paddingInlineStart}px`,
                      top: `${translationLayout.paddingBlockStart}px`,
                      width: `${translationLayout.contentWidth}px`,
                      height: `${translationLayout.contentHeight}px`,
                      columnWidth: `${translationLayout.contentWidth}px`,
                      columnGap: `${translationLayout.columnGap}px`,
                      transform: `translate3d(${-translationPageIndex * (
                        translationLayout.contentWidth + translationLayout.columnGap
                      )}px, 0, 0)`,
                      ...translationLayout.defaultStyle,
                    }}
                  >
                    {translationSegments.map((segment) => {
                      if (segment.kind === 'image') {
                        return <img
                          key={segment.id}
                          className={styles.translationImage}
                          data-source-image-id={segment.id}
                          src={segment.src}
                          alt={segment.alt}
                          title={segment.title}
                          style={segment.style}
                          draggable={false}
                        />;
                      }
                      const block = translatedBlockById.get(segment.id);
                      if (!block) return null;
                      return createElement(block.tag, {
                        key: block.id,
                        className: styles.translationBlock,
                        'data-source-block-id': block.id,
                        style: block.style,
                      }, translatedInlineContent(block));
                    })}
                  </div>
                </div>
              </div>
            </div>
          )}

          {settings?.tapToTurn && settings.flow === 'paginated' && ready && !error && (
            <>
              <button className={`${styles.tapZone} ${styles.tapZoneLeft}`}
                data-reader-wheel-page-zone
                onClick={(event) => { navigateReader('left'); event.currentTarget.blur(); }}
                title={t('Previous page')} aria-label={t('Previous page')}>
                <ChevronLeft size={30} aria-hidden="true" />
              </button>
              <button className={`${styles.tapZone} ${styles.tapZoneRight}`}
                data-reader-wheel-page-zone
                onClick={(event) => { navigateReader('right'); event.currentTarget.blur(); }}
                title={t('Next page')} aria-label={t('Next page')}>
                <ChevronRight size={30} aria-hidden="true" />
              </button>
            </>
          )}
        </section>
      </div>
      <footer className={styles.bottomBar}>
        <button className={styles.pageButton} onClick={() => navigateReader('prev')} title={t('Previous page')}>
          <ChevronLeft size={22} aria-hidden="true" />
        </button>
        <div className={styles.progressArea}>
          <div className={styles.progressMeta}>
            <span>{location.tocItem?.label || t('Book')}</span>
            <span>
              {location.pageItem?.label ? `${t('Page')} ${location.pageItem.label} · ` : ''}
              {location.location?.current && location.location?.total
                ? `${t('Location')} ${location.location.current}/${location.location.total} · ` : ''}
              {percent}%
            </span>
          </div>
          <input className={styles.progressSlider} type="range" min={0} max={1000}
            value={Math.round(progress * 1000)} list="reader-section-marks"
            aria-label={t('Reading progress')}
            onChange={(event) => {
              const fraction = Number(event.target.value) / 1000;
              restoreInlineTranslations();
              dismissSelection();
              setLocation((current) => ({ ...current, fraction }));
              void viewRef.current?.goToFraction(fraction);
            }} />
          <datalist id="reader-section-marks">
            {sectionFractions.map((fraction) => <option key={fraction} value={Math.round(fraction * 1000)} />)}
          </datalist>
        </div>
        <button className={styles.pageButton} onClick={() => navigateReader('next')} title={t('Next page')}>
          <ChevronRight size={22} aria-hidden="true" />
        </button>
      </footer>
      {saveError && <div className={styles.saveError} role="alert">
        {t('Could not save reading position. It will be retried automatically.')}
      </div>}
    </main>
  );
}
type ReaderSidePanelProps = {
  panel: Exclude<ReaderPanel, null>;
  onClose: () => void;
  toc: Array<TocItem & { depth: number }>;
  onNavigate: (target: string) => void;
  searchText: string;
  setSearchText: (value: string) => void;
  runSearch: () => void;
  searching: boolean;
  searchProgress: number;
  searchResults: SearchResult[];
  bookmarks: ReaderBookmark[];
  openBookmark: (bookmark: ReaderBookmark) => void;
  deleteBookmark: (bookmarkId: string) => void;
  annotations: FoliateAnnotation[];
  showAnnotation: (annotation: FoliateAnnotation) => void;
  removeAnnotation: (annotation: FoliateAnnotation) => void;
  updateAnnotationNote: (annotation: FoliateAnnotation) => void;
  settings: ReaderSettings | null;
  updateSettings: (patch: Partial<ReaderSettings>) => void;
};

function ReaderSidePanel(props: ReaderSidePanelProps) {
  const t = useT();
  const { panel, onClose } = props;
  const title = {
    toc: t('Table of contents'), search: t('Search in book'),
    bookmarks: t('Bookmarks'), notes: t('Highlights and notes'),
    settings: t('Reader settings'), translation: t('Page translation'),
  }[panel];
  return (
    <aside className={styles.sidePanel} aria-label={title}>
      <header className={styles.panelHeader}>
        <h2>{title}</h2>
        <button className={styles.iconButton} onClick={onClose} title={t('Close')}>
          <X size={18} aria-hidden="true" />
        </button>
      </header>

      {panel === 'toc' && (
        <div className={styles.panelList}>
          {props.toc.length === 0 && <p className={styles.muted}>{t('No contents found.')}</p>}
          {props.toc.map((item, index) => (
            <button key={`${item.href}-${index}`} className={styles.listButton}
              style={{ paddingInlineStart: `${12 + item.depth * 16}px` }}
              disabled={!item.href}
              onClick={() => item.href && props.onNavigate(item.href)}>
              {item.label || t('Untitled')}
            </button>
          ))}
        </div>
      )}

      {panel === 'search' && (
        <div className={styles.panelBody}>
          <form className={styles.searchForm} onSubmit={(event) => { event.preventDefault(); props.runSearch(); }}>
            <input value={props.searchText} onChange={(event) => props.setSearchText(event.target.value)}
              placeholder={t('Search in book')} aria-label={t('Search in book')} />
            <button type="submit" disabled={!props.searchText.trim() || props.searching}>{t('Search')}</button>
          </form>
          {props.searching && <progress max={1} value={props.searchProgress} />}
          <div className={styles.panelList}>
            {props.searchResults.map((result, index) => (
              <button key={`${result.cfi}-${index}`} className={styles.searchResult}
                onClick={() => props.onNavigate(result.cfi)}>
                <strong>{result.label || t('Book')}</strong>
                <span>{excerptText(result.excerpt)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {panel === 'bookmarks' && (
        <div className={styles.panelList}>
          {props.bookmarks.length === 0 && <p className={styles.muted}>{t('No bookmarks yet.')}</p>}
          {props.bookmarks.map((bookmark) => (
            <div className={styles.savedItem} key={bookmark.bookmark_id}>
              <button onClick={() => props.openBookmark(bookmark)}>
                <strong>{bookmark.chapter || t('Bookmark')}</strong>
                <span>{Math.round(bookmark.progression * 100)}%
                  {bookmark.label ? ` · ${bookmark.label}` : ''}</span>
              </button>
              <button className={styles.deleteButton} onClick={() => props.deleteBookmark(bookmark.bookmark_id)}
                title={t('Delete')}><Trash2 size={16} aria-hidden="true" /></button>
            </div>
          ))}
        </div>
      )}

      {panel === 'notes' && (
        <div className={styles.panelList}>
          {props.annotations.length === 0 && <p className={styles.muted}>{t('No highlights yet.')}</p>}
          {props.annotations.map((annotation) => (
            <div className={styles.annotationItem} key={annotation.id ?? annotation.value}>
              <button onClick={() => props.showAnnotation(annotation)}>
                <span className={styles.annotationText}>{annotation.text || t('Highlight')}</span>
                {annotation.note && <span className={styles.annotationNote}>{annotation.note}</span>}
              </button>
              <div className={styles.itemActions}>
                <button onClick={() => props.updateAnnotationNote(annotation)}>{t('Note')}</button>
                <button className={styles.deleteButton} onClick={() => props.removeAnnotation(annotation)}
                  title={t('Delete')}><Trash2 size={16} aria-hidden="true" /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      {panel === 'settings' && props.settings && (
        <ReaderSettingsPanel settings={props.settings} update={props.updateSettings} />
      )}
      {panel === 'translation' && props.settings && (
        <div className={styles.translationPanel}>
          <ReaderTranslationSettings settings={props.settings} update={props.updateSettings} />
        </div>
      )}
    </aside>
  );
}
function ReaderSettingsPanel({ settings, update }: {
  settings: ReaderSettings;
  update: (patch: Partial<ReaderSettings>) => void;
}) {
  const t = useT();
  return (
    <div className={styles.settingsPanel}>
      <label>{t('Reading mode')}
        <select value={settings.flow} onChange={(event) => update({ flow: event.target.value as ReaderSettings['flow'] })}>
          <option value="paginated">{t('Pages')}</option>
          <option value="scrolled">{t('Continuous scroll')}</option>
        </select>
      </label>
      <label>{t('Columns')}
        <select value={settings.spread} onChange={(event) => update({ spread: event.target.value as ReaderSettings['spread'] })}
          disabled={settings.flow === 'scrolled'}>
          <option value="nonespread">{t('One column')}</option>
          <option value="spread">{t('Two columns')}</option>
        </select>
      </label>
      <label>{t('Theme')}
        <select value={settings.theme} onChange={(event) => update({ theme: event.target.value as ReaderSettings['theme'] })}>
          <option value="lightTheme">{t('Light')}</option>
          <option value="sepiaTheme">{t('Sepia')}</option>
          <option value="darkTheme">{t('Dark')}</option>
          <option value="blackTheme">{t('Black')}</option>
        </select>
      </label>
      <label>{t('Font')}
        <select value={settings.font} onChange={(event) => update({ font: event.target.value as ReaderSettings['font'] })}>
          <option value="default">{t('Publisher / serif')}</option>
          <option value="Arial">Arial</option>
          <option value="Yahei">Microsoft YaHei</option>
          <option value="SimSun">SimSun</option>
          <option value="KaiTi">KaiTi</option>
        </select>
      </label>
      <label>{t('Font size')} <output>{settings.fontSize}%</output>
        <input type="range" min={FONT_MIN} max={FONT_MAX} value={settings.fontSize}
          onChange={(event) => update({ fontSize: Number(event.target.value) })} />
      </label>
      <label>{t('Line height')} <output>{(settings.lineHeight / 100).toFixed(1)}</output>
        <input type="range" min={100} max={220} step={5} value={settings.lineHeight}
          onChange={(event) => update({ lineHeight: Number(event.target.value) })} />
      </label>
      <label>{t('Page margins')} <output>{settings.margin}px</output>
        <input type="range" min={0} max={80} step={4} value={settings.margin}
          onChange={(event) => update({ margin: Number(event.target.value) })} />
      </label>
      <label>{t('Text width')} <output>{settings.maxInlineSize}px</output>
        <input type="range" min={420} max={1200} step={20} value={settings.maxInlineSize}
          onChange={(event) => update({ maxInlineSize: Number(event.target.value) })} />
      </label>
      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.justifyText}
          onChange={(event) => update({ justifyText: event.target.checked })} />
        {t('Justify text')}
      </label>
      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.animated}
          onChange={(event) => update({ animated: event.target.checked })} />
        {t('Animated page turns')}
      </label>
      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.tapToTurn}
          onChange={(event) => update({ tapToTurn: event.target.checked })} />
        {t('Turn pages by clicking the left or right side')}
      </label>
      <div className={styles.settingsHint}>
        <AlignJustify size={18} aria-hidden="true" />
        <span>{t('Reader settings are saved to your account and follow you across devices.')}</span>
      </div>
      <div className={styles.settingsHint}>
        <Columns2 size={18} aria-hidden="true" />
        <span>{t('Page count changes with font, margins, and window size; progress remains stable.')}</span>
      </div>
    </div>
  );
}
