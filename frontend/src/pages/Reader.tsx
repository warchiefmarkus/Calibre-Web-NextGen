import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'wouter';
import {
  AlignJustify, Bookmark, BookOpen, ChevronLeft, ChevronRight,
  Columns2, Highlighter, List, Maximize, Search, Settings,
  Square, StickyNote, Trash2, Volume2, X,
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import { apiDelete, apiGet, apiPatch, apiPost, resourceUrl } from '../lib/api';
import {
  useBook, useBookmark, useCreateReaderBookmark, useDeleteReaderBookmark,
  useReaderBookmarks, useReaderSettings,
  useSaveReaderSettings, type ReaderBookmark, type ReaderSettings,
} from '../lib/queries';
import { useT } from '../lib/i18n';
import { parseFb2ScrollBookmark, useReadingPositionSaver } from '../lib/readerProgress';
import styles from './Reader.module.css';

// Vendored at an exact upstream commit; see vendor/foliate-js/UPSTREAM.md.
import '../vendor/foliate-js/view.js';
// @ts-expect-error foliate-js intentionally ships browser JavaScript without declarations.
import { Overlayer } from '../vendor/foliate-js/overlayer.js';
type TocItem = { label?: string; href?: string; subitems?: TocItem[] };
type SearchExcerpt = { pre: string; match: string; post: string };
type SearchResult = { cfi: string; label: string; excerpt: SearchExcerpt };
type ReaderPanel = 'toc' | 'search' | 'bookmarks' | 'notes' | 'settings' | null;

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
};
type FoliateView = HTMLElement & {
  book?: {
    toc?: TocItem[];
    sections?: unknown[];
    metadata?: { title?: unknown; author?: unknown; language?: string };
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

function readerCss(settings: ReaderSettings): string {
  const theme = THEME[settings.theme];
  return `
    :root { color-scheme: ${settings.theme === 'lightTheme' || settings.theme === 'sepiaTheme' ? 'light' : 'dark'}; }
    html, body { background: ${theme.background} !important; color: ${theme.text} !important; }
    body { font-family: ${FONT_FAMILY[settings.font]} !important; font-size: ${settings.fontSize}% !important;
      line-height: ${settings.lineHeight / 100} !important; text-align: left !important; }
    a { color: ${theme.link} !important; }
    img, svg, video { max-width: 100% !important; }
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
  const positionQuery = useBookmark(id, fmt);
  const bookmarksQuery = useReaderBookmarks(id, fmt);
  const saveSettings = useSaveReaderSettings();
  const createBookmark = useCreateReaderBookmark(id, fmt);
  const deleteBookmark = useDeleteReaderBookmark(id, fmt);

  const { schedule: schedulePosition, saveError } = useReadingPositionSaver(id, fmt, 450);

  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<FoliateView | null>(null);
  const searchRunRef = useRef(0);
  const annotationsRef = useRef<Map<string, FoliateAnnotation>>(new Map());
  const currentRef = useRef<FoliateLocation>({ fraction: 0 });

  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<ReaderPanel>(null);
  const [title, setTitle] = useState('');
  const [toc, setToc] = useState<Array<TocItem & { depth: number }>>([]);
  const [sectionFractions, setSectionFractions] = useState<number[]>([]);
  const [location, setLocation] = useState<FoliateLocation>({ fraction: 0 });
  const [settings, setSettings] = useState<ReaderSettings | null>(null);
  const [searchText, setSearchText] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchProgress, setSearchProgress] = useState(0);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [pendingSelection, setPendingSelection] = useState<{ value: string; text: string } | null>(null);
  const [annotations, setAnnotations] = useState<FoliateAnnotation[]>([]);
  const [selectedAnnotation, setSelectedAnnotation] = useState<FoliateAnnotation | null>(null);
  const [speaking, setSpeaking] = useState(false);

  const applySettings = useCallback((next: ReaderSettings) => {
    const view = viewRef.current;
    const renderer = view?.renderer;
    if (!renderer) return;
    renderer.setAttribute('flow', next.flow);
    renderer.setAttribute('margin', `${next.margin}px`);
    const pageGapPercent = Math.max(3, Math.min(12, 3 + next.margin / 4));
    renderer.setAttribute('gap', `${pageGapPercent}%`);
    renderer.setAttribute('max-inline-size', `${next.maxInlineSize}px`);
    renderer.setAttribute('max-column-count', String(next.spread === 'nonespread' ? 1 : next.maxColumnCount));
    renderer.toggleAttribute('animated', next.animated && next.flow === 'paginated');
    renderer.setAttribute('background', THEME[next.theme].background);
    renderer.setStyles?.(readerCss(next));
  }, []);

  const updateSettings = useCallback((patch: Partial<ReaderSettings>) => {
    setSettings((current) => {
      if (!current) return current;
      const next = { ...current, ...patch };
      applySettings(next);
      saveSettings.mutate(patch);
      return next;
    });
  }, [applySettings, saveSettings]);


  const dismissSelection = useCallback(() => {
    setPendingSelection(null);
    viewRef.current?.deselect();
  }, []);

  const navigate = useCallback((action: 'prev' | 'next' | 'left' | 'right') => {
    dismissSelection();
    const view = viewRef.current;
    if (!view) return;
    if (action === 'prev') void view.prev();
    else if (action === 'next') void view.next();
    else if (action === 'left') void view.goLeft();
    else void view.goRight();
  }, [dismissSelection]);
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
    const initialSettings = settingsQuery.data.reader;
    setSettings(initialSettings);

    const onRelocate = (event: Event) => {
      dismissSelection();
      const detail = (event as CustomEvent<FoliateLocation>).detail;
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
        const text = selection.toString().replace(/\s+/g, ' ').trim();
        if (!text) return;
        setPendingSelection({ value: view.getCFI(index, range), text });
      };
      doc.addEventListener('mouseup', readSelection);
      doc.addEventListener('keyup', readSelection);
      doc.addEventListener('keydown', onReaderKeyDown);
    };
    const onLoad = (event: Event) => {
      const detail = (event as CustomEvent<{ doc: Document; index: number }>).detail;
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
        setTitle(formatLanguageMap(view.book?.metadata?.title) || bookQuery.data?.title || t('Untitled'));
        setToc(flattenToc(view.book?.toc ?? []));
        setSectionFractions(view.getSectionFractions());
        const annotationPayload = await apiGet<{ annotations: ServerAnnotation[] }>(
          `/annotations/${id}/data.json`,
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
    settingsQuery.data, schedulePosition, dismissSelection, t]);
  function onReaderKeyDown(event: KeyboardEvent) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      navigate('left');
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      navigate('right');
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

  const removeAnnotation = async (annotation: FoliateAnnotation) => {
    if (!annotation.id) return;
    await apiDelete(`/annotations/${id}/${encodeURIComponent(annotation.id)}`);
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
      { note_text: note || null },
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
          <button onClick={dismissSelection}>
            <X size={17} aria-hidden="true" /> {t('Cancel')}
          </button>
        </div>
      )}

      <div className={styles.workspace}>
        {panel && <ReaderSidePanel
          panel={panel} onClose={() => setPanel(null)} toc={toc}
          onNavigate={(target) => { dismissSelection(); void viewRef.current?.goTo(target); setPanel(null); }}
          searchText={searchText} setSearchText={setSearchText} runSearch={() => void runSearch()}
          searching={searching} searchProgress={searchProgress} searchResults={searchResults}
          bookmarks={bookmarks} openBookmark={openReaderBookmark}
          deleteBookmark={(bookmarkId) => deleteBookmark.mutate(bookmarkId)}
          annotations={annotations}
          showAnnotation={(annotation) => { void viewRef.current?.showAnnotation(annotation); setPanel(null); }}
          removeAnnotation={(annotation) => void removeAnnotation(annotation)}
          updateAnnotationNote={(annotation) => void updateAnnotationNote(annotation)}
          settings={settings} updateSettings={updateSettings}
        />}
        <section className={styles.stage} aria-label={t('Book content')}>
          {!ready && !error && <div className={styles.stageLoading}><SpinnerCentered size={44} /></div>}
          {error && <EmptyState message={error} />}
          <div ref={hostRef} className={styles.host} data-ready={ready ? 'true' : 'false'} />
          {settings?.tapToTurn && settings.flow === 'paginated' && ready && !error && (
            <>
              <button className={`${styles.tapZone} ${styles.tapZoneLeft}`}
                onClick={() => navigate('left')} title={t('Previous page')}
                aria-label={t('Previous page')}>
                <ChevronLeft size={30} aria-hidden="true" />
              </button>
              <button className={`${styles.tapZone} ${styles.tapZoneRight}`}
                onClick={() => navigate('right')} title={t('Next page')}
                aria-label={t('Next page')}>
                <ChevronRight size={30} aria-hidden="true" />
              </button>
            </>
          )}
        </section>
      </div>
      <footer className={styles.bottomBar}>
        <button className={styles.pageButton} onClick={() => navigate('prev')} title={t('Previous page')}>
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
              dismissSelection();
              setLocation((current) => ({ ...current, fraction }));
              void viewRef.current?.goToFraction(fraction);
            }} />
          <datalist id="reader-section-marks">
            {sectionFractions.map((fraction) => <option key={fraction} value={Math.round(fraction * 1000)} />)}
          </datalist>
        </div>
        <button className={styles.pageButton} onClick={() => navigate('next')} title={t('Next page')}>
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
    settings: t('Reader settings'),
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
