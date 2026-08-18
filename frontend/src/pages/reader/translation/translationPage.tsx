import { Fragment, createElement, type CSSProperties, type ReactNode } from 'react';
import type {
  ReaderSettings, ReaderTranslationBlock, ReaderTranslationProfile, ReaderTranslationResponse,
  ReaderTranslationRun, ReaderTranslationRunMark,
} from '../../../lib/queries';
import type { FoliateRenderer } from '../FoliateEngine';
import styles from '../../Reader.module.css';

const TRANSLATABLE_SELECTOR = 'h1, h2, h3, h4, h5, h6, p, li, blockquote, pre, figcaption, div.paragraph';
const TRANSLATION_CONTENT_SELECTOR = `${TRANSLATABLE_SELECTOR}, img`;
const MAX_VISIBLE_TRANSLATION_CHARS = 8_000;
const MAX_VISIBLE_TRANSLATION_BLOCKS = 80;
const MAX_INLINE_TRANSLATION_RUNS = 200;
const MAX_INLINE_TRANSLATION_RUN_CHARS = 1200;
export const TRANSLATION_DEBOUNCE_MS = 500;

export type TranslationViewport = {
  frameRect: DOMRect;
  rendererRect: DOMRect;
  viewportRect: { left: number; right: number; top: number; bottom: number };
};

export type TranslationBlockStyle = Pick<CSSProperties,
  'fontFamily' | 'fontSize' | 'fontWeight' | 'fontStyle' | 'lineHeight'
  | 'letterSpacing' | 'textAlign' | 'textIndent' | 'textTransform'
  | 'marginBlockStart' | 'marginBlockEnd'>;

export type SourceTranslationRun = ReaderTranslationRun & { href?: string };
export type StyledTranslationBlock = ReaderTranslationBlock & {
  style?: TranslationBlockStyle;
  sourceRuns?: SourceTranslationRun[];
};

export type PreservedImageStyle = Pick<CSSProperties,
  'width' | 'height' | 'maxWidth' | 'maxHeight' | 'objectFit' | 'display'
  | 'marginBlockStart' | 'marginBlockEnd' | 'marginInlineStart' | 'marginInlineEnd'
  | 'borderRadius'>;

export type TranslationTextSegment = { kind: 'text'; id: string };
export type TranslationImageSegment = {
  kind: 'image';
  id: string;
  src: string;
  alt: string;
  title?: string;
  style: PreservedImageStyle;
};
export type TranslationContentSegment = TranslationTextSegment | TranslationImageSegment;

export type TranslationPageLayout = {
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

export type VisibleTranslationPage = {
  blocks: ReaderTranslationBlock[];
  styles: Record<string, TranslationBlockStyle>;
  sourceRuns: Record<string, SourceTranslationRun[]>;
  segments: TranslationContentSegment[];
  layout: TranslationPageLayout;
};

export type TranslationPreloadJob = {
  key: string;
  settings: ReaderSettings;
  profileRevision: string;
  blocks: ReaderTranslationBlock[];
};

export type TranslationSentenceCarry = {
  blockId: string;
  text: string;
};

export type TranslationPreloadTask = {
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

export function normalizeLanguageCode(value: unknown): string {
  const raw = Array.isArray(value) ? value[0] : value;
  if (typeof raw !== 'string') return '';
  const normalized = raw.trim().toLowerCase().replace('_', '-');
  if (!normalized) return '';
  const primary = normalized.split('-')[0];
  return LANGUAGE_ALIASES[normalized] ?? LANGUAGE_ALIASES[primary] ?? primary;
}

export function canExtractTranslationPageOffset(renderer: FoliateRenderer, pageOffset: number): boolean {
  if (pageOffset === 0) return true;
  if (!Number.isInteger(pageOffset) || pageOffset < 0) return false;
  const size = Number(renderer.size);
  if (!Number.isFinite(size) || size <= 0) return false;
  if (renderer.getAttribute('flow') === 'paginated') {
    const page = Number(renderer.page);
    const pages = Number(renderer.pages);
    return Number.isFinite(page) && Number.isFinite(pages)
      && page + pageOffset <= pages - 2;
  }
  const start = Number(renderer.start);
  const viewSize = Number(renderer.viewSize);
  return Number.isFinite(start) && Number.isFinite(viewSize)
    && start + pageOffset * size < viewSize - 2;
}

export function translationViewport(
  renderer: FoliateRenderer, doc: Document, pageOffset = 0,
): TranslationViewport | null {
  const frame = doc.defaultView?.frameElement;
  if (!(frame instanceof HTMLElement) || !canExtractTranslationPageOffset(renderer, pageOffset)) return null;
  const rendererRect = renderer.getBoundingClientRect();
  const frameRect = frame.getBoundingClientRect();
  if (renderer.getAttribute('flow') !== 'paginated') {
    const shift = pageOffset * Math.max(1, Number(renderer.size) || rendererRect.height);
    return {
      frameRect,
      rendererRect,
      viewportRect: {
        left: rendererRect.left, right: rendererRect.right,
        top: rendererRect.top + shift,
        bottom: rendererRect.bottom + shift,
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

export function rectIntersectsViewport(rect: DOMRect, viewport: TranslationViewport): boolean {
  const left = viewport.frameRect.left + rect.left;
  const right = viewport.frameRect.left + rect.right;
  const top = viewport.frameRect.top + rect.top;
  const bottom = viewport.frameRect.top + rect.bottom;
  return rect.width > 0 && rect.height > 0
    && right > viewport.viewportRect.left && left < viewport.viewportRect.right
    && bottom > viewport.viewportRect.top && top < viewport.viewportRect.bottom;
}

export function intersectingOuterRects(element: Element, viewport: TranslationViewport): Array<{
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

export type ExtractedInlineRuns = {
  text: string;
  sourceRuns: SourceTranslationRun[];
  requestRuns: ReaderTranslationRun[];
};

export function inlineRunContext(node: Text, block: Element): {
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

export function sameInlineRunFormat(
  left: Omit<SourceTranslationRun, 'id' | 'text'>,
  right: Omit<SourceTranslationRun, 'id' | 'text'>,
): boolean {
  return left.href === right.href
    && (left.break_before ?? 0) === (right.break_before ?? 0)
    && JSON.stringify(left.marks ?? []) === JSON.stringify(right.marks ?? []);
}

export function visibleBlockRuns(
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


export function computedTranslationStyle(element: Element): TranslationBlockStyle {
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

export function preservedImageSegment(
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

export function clampLayoutInset(value: number, pageSize: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(0, Math.min(pageSize * 0.3, value));
}

export function extractVisiblePage(renderer?: FoliateRenderer, pageOffset = 0): VisibleTranslationPage | null {
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

export function looksLikeUkrainian(blocks: ReaderTranslationBlock[]): boolean {
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

export function sourceAlreadyMatchesTarget(
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

export function translationProfileRevision(profile: ReaderTranslationProfile | null | undefined): string {
  if (!profile) return '';
  return JSON.stringify({
    updatedAt: profile.updated_at,
    baseUrl: profile.base_url,
    endpointPath: profile.endpoint_path,
    model: profile.model,
    temperature: profile.temperature,
    maxOutputTokens: profile.max_output_tokens,
    jsonMode: profile.json_mode,
  });
}

export function translationPageKey(
  settings: ReaderSettings,
  blocks: ReaderTranslationBlock[],
  profileRevision: string,
): string {
  return JSON.stringify({
    profile: settings.translationProfileId,
    profileRevision,
    source: settings.translationSourceLanguage,
    target: settings.translationTargetLanguage,
    mode: settings.translationMode,
    prompt: settings.translationPrompt,
    blocks: blocks.map((block) => [block.id, block.text, block.runs]),
  });
}

export function translationSourcePageId(
  renderer: FoliateRenderer | undefined,
  pageOffset = 0,
): string | null {
  if (!renderer || !canExtractTranslationPageOffset(renderer, pageOffset)) return null;
  const contentIndex = [...(renderer.getContents?.() ?? [])]
    .sort((left, right) => left.index - right.index)[0]?.index;
  if (!Number.isFinite(contentIndex)) return null;
  const flow = renderer.getAttribute('flow') === 'scrolled' ? 'scrolled' : 'paginated';
  if (flow === 'paginated') {
    const page = Number(renderer.page);
    return Number.isFinite(page) ? `${contentIndex}:${flow}:${page + pageOffset}` : null;
  }
  const size = Number(renderer.size);
  const start = Number(renderer.start);
  if (!Number.isFinite(size) || size <= 0 || !Number.isFinite(start)) return null;
  return `${contentIndex}:${flow}:${Math.round(start + pageOffset * size)}`;
}

export function translationPageCacheKey(
  settings: ReaderSettings,
  profileRevision: string,
  pageId: string | null,
): string | null {
  if (!pageId) return null;
  return JSON.stringify({
    pageId,
    profile: settings.translationProfileId,
    profileRevision,
    source: settings.translationSourceLanguage,
    target: settings.translationTargetLanguage,
    mode: settings.translationMode,
    prompt: settings.translationPrompt,
    flow: settings.flow,
    spread: settings.spread,
    font: settings.font,
    fontSize: settings.fontSize,
    lineHeight: settings.lineHeight,
    margin: settings.margin,
    maxColumnCount: settings.maxColumnCount,
    maxInlineSize: settings.maxInlineSize,
  });
}

export function mergeSentenceCarry(carry: string, pageStart: string): string {
  const prefix = carry.replace(/\s+/g, ' ').trim();
  const current = pageStart.replace(/\s+/g, ' ').trim();
  if (!prefix) return current;
  if (!current) return prefix;
  const lowerPrefix = prefix.toLocaleLowerCase();
  const lowerCurrent = current.toLocaleLowerCase();
  if (lowerCurrent.startsWith(lowerPrefix)) return current;
  if (lowerPrefix.endsWith(lowerCurrent)) return prefix;

  const prefixWords = prefix.split(' ');
  const currentWords = current.split(' ');
  const normalizeWord = (word: string) => word.toLocaleLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');
  let overlap = 0;
  for (let count = Math.min(32, prefixWords.length, currentWords.length); count >= 2; count -= 1) {
    const left = prefixWords.slice(-count).map(normalizeWord);
    const right = currentWords.slice(0, count).map(normalizeWord);
    if (left.every((word, index) => word && word === right[index])) {
      overlap = count;
      break;
    }
  }
  return `${prefix} ${currentWords.slice(overlap).join(' ')}`.trim();
}

export function applySentenceCarry(
  blocks: ReaderTranslationBlock[],
  carry?: TranslationSentenceCarry,
): ReaderTranslationBlock[] {
  if (!carry) return blocks;
  const index = blocks.findIndex((block) => block.id === carry.blockId);
  if (index < 0) return blocks;
  return blocks.map((block, blockIndex) => blockIndex === index ? {
    id: block.id,
    tag: block.tag,
    text: mergeSentenceCarry(carry.text, block.text),
  } : block);
}

export function splitTrailingSentenceForNext(
  blocks: ReaderTranslationBlock[],
  nextBlocks: ReaderTranslationBlock[],
): { blocks: ReaderTranslationBlock[]; carry?: TranslationSentenceCarry } {
  const last = blocks[blocks.length - 1];
  const next = nextBlocks[0];
  if (!last || !next || last.id !== next.id
      || !['p', 'li', 'blockquote'].includes(last.tag)) return { blocks };
  const text = last.text.replace(/\s+/g, ' ').trim();
  if (!text || /[.!?…]["'’”»)\]}]*$/u.test(text)) return { blocks };

  const boundary = /[.!?…]["'’”»)\]}]*(?:\s+|$)/gu;
  let completedEnd = 0;
  for (const match of text.matchAll(boundary)) {
    completedEnd = (match.index ?? 0) + match[0].length;
  }
  const completed = text.slice(0, completedEnd).trimEnd();
  const trailing = text.slice(completedEnd).trim();
  if (!completed || trailing.length < 8 || !/[\p{L}\p{N}]/u.test(trailing)) return { blocks };

  return {
    blocks: blocks.map((block) => block.id === last.id ? {
      id: block.id,
      tag: block.tag,
      text: completed,
    } : block),
    carry: { blockId: last.id, text: trailing },
  };
}

export function alignTranslatedBlocks(
  sourceBlocks: ReaderTranslationBlock[],
  translatedBlocks: ReaderTranslationBlock[],
): ReaderTranslationBlock[] {
  const translatedById = new Map(translatedBlocks.map((block) => [block.id, block]));
  return sourceBlocks.map((source, index) => {
    const translated = translatedById.get(source.id) ?? translatedBlocks[index];
    return translated ? { ...translated, id: source.id, tag: source.tag } : source;
  });
}

export function translationRequestBlocks(
  mode: ReaderSettings['translationMode'],
  blocks: ReaderTranslationBlock[],
): ReaderTranslationBlock[] {
  if (mode === 'structured') return blocks;
  return [{
    id: '__page__',
    tag: 'p',
    text: blocks.map((block) => block.text.trim()).filter(Boolean).join('\n\n'),
  }];
}

export function simpleTranslationParts(
  sourceBlocks: ReaderTranslationBlock[],
  translatedText: string,
): string[] {
  const text = translatedText.replace(/\r\n?/g, '\n').trim();
  if (sourceBlocks.length <= 1) return [text];
  const paragraphs = text.split(/\n\s*\n+/).map((part) => part.trim()).filter(Boolean);
  if (paragraphs.length === sourceBlocks.length) return paragraphs;

  const weights = sourceBlocks.map((block) => Math.max(1, block.text.trim().length));
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
  const result: string[] = [];
  let cursor = 0;
  let cumulativeWeight = 0;
  for (let index = 0; index < sourceBlocks.length - 1; index += 1) {
    cumulativeWeight += weights[index];
    const remainingBlocks = sourceBlocks.length - index - 1;
    const minimumEnd = Math.min(text.length, cursor + 1);
    const maximumEnd = Math.max(minimumEnd, text.length - remainingBlocks);
    const target = Math.max(minimumEnd, Math.min(
      maximumEnd, Math.round(text.length * cumulativeWeight / totalWeight),
    ));
    const forward = text.slice(target, Math.min(maximumEnd, target + 120)).search(/\s/);
    const backwardMatch = text.slice(Math.max(minimumEnd, target - 120), target).match(/\s(?=\S*$)/);
    const backward = backwardMatch?.index;
    const backwardStart = Math.max(minimumEnd, target - 120);
    const boundary = forward >= 0
      ? target + forward
      : backward !== undefined
        ? backwardStart + backward
        : target;
    result.push(text.slice(cursor, boundary).trim());
    cursor = boundary;
  }
  result.push(text.slice(cursor).trim());
  return result;
}

export function translationResponseForMode(
  mode: ReaderSettings['translationMode'],
  sourceBlocks: ReaderTranslationBlock[],
  response: ReaderTranslationResponse,
): ReaderTranslationResponse {
  if (mode === 'structured' || response.skipped && response.blocks.length === sourceBlocks.length) {
    return response;
  }
  const translatedText = response.blocks.map((block) => block.text).join('\n\n');
  const parts = simpleTranslationParts(sourceBlocks, translatedText);
  return {
    ...response,
    blocks: sourceBlocks.map((block, index) => ({
      id: block.id,
      tag: block.tag,
      text: parts[index] || block.text,
    })),
  };
}

export function safeTranslationHref(value?: string): string | undefined {
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

export function translatedInlineContent(block: StyledTranslationBlock): ReactNode {
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

