import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import { apiDelete, apiGet, apiPatch, apiPost, resourceUrl } from '../lib/api';
import {
  useBook, useBookmark, useCreateReaderBookmark, useDeleteReaderBookmark,
  useReaderBookmarks, useReaderBookState, useReaderSettings, useReaderTranslationProfiles,
  useSaveReaderBookState, useSaveReaderSettings, translateReaderPage, type ReaderBookmark, type ReaderSettings,
  type ReaderTranslationBlock, type ReaderTranslationResponse,
} from '../lib/queries';
import { useT } from '../lib/i18n';
import { formatReadingProgress, parseFb2ScrollBookmark, useReadingPositionSaver } from '../lib/readerProgress';
import styles from './Reader.module.css';

// Foliate-specific engine details live behind the reader module boundary.
import { allowsWheelPageTurn, createFoliateView, flattenToc, formatLanguageMap, isReaderTypingTarget,
  readerFileName, readerMime, scrolledPageTurnDistance, type FoliateAnnotation, type FoliateLocation,
  type FoliateView, type SearchExcerpt, type SearchResult, type TocItem } from './reader/FoliateEngine';
import { ReaderSidePanel, type ReaderPanel } from './reader/ReaderSidePanel';
import { ReaderToolbar } from './reader/ReaderToolbar';
import { ReaderBottomBar } from './reader/ReaderBottomBar';
import { SelectionActions } from './reader/SelectionActions';
import { AnnotationComposer } from './reader/annotations/AnnotationComposer';
import { drawFoliateHighlight } from './reader/annotations/draw';
import { annotationColor, type AnnotationEditorState, type HighlightColor,
  type ServerAnnotation } from './reader/annotations/types';
import { useReaderFullscreen } from './reader/fullscreen';
import { findMoonAnchorCfi } from './reader/useMoonRestore';
import { useReadingMovement } from './reader/useReadingMovement';
import { TranslationOverlay } from './reader/translation/TranslationOverlay';
import { FONT_MAX, FONT_MIN, READER_THEME, readerCss } from './reader/settings/readerStyle';
import {
  TRANSLATION_DEBOUNCE_MS, alignTranslatedBlocks, applySentenceCarry, extractVisiblePage,
  normalizeLanguageCode, sourceAlreadyMatchesTarget, splitTrailingSentenceForNext,
  translationPageCacheKey, translationPageKey,
  translationProfileRevision, translationRequestBlocks, translationResponseForMode,
  translationSourcePageId, type StyledTranslationBlock, type TranslationContentSegment,
  type TranslationPageLayout, type TranslationPreloadJob, type TranslationPreloadTask,
  type TranslationSentenceCarry,
} from './reader/translation/translationPage';
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
const READER_WHEEL_THRESHOLD_PX = 48;
const READER_WHEEL_COOLDOWN_MS = 320;
const READER_WHEEL_IDLE_RESET_MS = 160;

function chatGptSelectedTextUrl(text: string): string {
  return `https://chatgpt.com/?q=${encodeURIComponent(text.trim())}`;
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
  const bookStateQuery = useReaderBookState(id, fmt);
  const translationProfilesQuery = useReaderTranslationProfiles();
  const positionQuery = useBookmark(id, fmt);
  const bookmarksQuery = useReaderBookmarks(id, fmt);
  const saveSettings = useSaveReaderSettings();
  const saveBookState = useSaveReaderBookState(id, fmt);
  const createBookmark = useCreateReaderBookmark(id, fmt);
  const deleteBookmark = useDeleteReaderBookmark(id, fmt);

  const { schedule: schedulePosition, saveError, savedPositionFraction } = useReadingPositionSaver(id, fmt, 450);
  const { movementRef: readingMovementRef, markReadingMovement, resetReadingMovement } = useReadingMovement();

  const shellRef = useRef<HTMLElement>(null);
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
  const translationSentenceCarryRef = useRef<Map<string, TranslationSentenceCarry>>(new Map());
  const translationOverlayRef = useRef<HTMLDivElement>(null);
  const translationPagerContentRef = useRef<HTMLDivElement>(null);
  const translationBlocksRef = useRef<StyledTranslationBlock[]>([]);
  const translationPageIndexRef = useRef(0);
  const translationPageCountRef = useRef(1);
  const translationLandingRef = useRef<'first' | 'last' | null>(null);
  const translationTransitionRef = useRef(false);
  const translationSkippedRef = useRef(false);
  const pendingSelectionRangeRef = useRef<{ range: Range; doc: Document } | null>(null);
  const inlineTranslationPatchesRef = useRef<InlineTranslationPatch[]>([]);

  const { fullscreenSupported, isFullscreen, toggleFullscreen } = useReaderFullscreen(shellRef);

  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<ReaderPanel>(null);
  const [title, setTitle] = useState('');
  const [bookLanguage, setBookLanguage] = useState('');
  const [bookRtl, setBookRtl] = useState(false);
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
  const [annotationEditor, setAnnotationEditor] = useState<AnnotationEditorState | null>(null);
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

  const closePanel = useCallback(() => setPanel(null), []);
  const closeAnnotationEditor = useCallback(() => setAnnotationEditor(null), []);

  const openEditAnnotation = useCallback((annotation: FoliateAnnotation) => {
    if (annotation.unanchored) {
      setAnnotationEditor({
        mode: 'standalone', annotation, color: 'yellow',
        note: annotation.note ?? '', focusNote: true,
      });
      return;
    }
    setAnnotationEditor({
      mode: 'edit', annotation, color: annotationColor(annotation.color),
      note: annotation.note ?? '', focusNote: true,
    });
  }, []);

  const activeTranslationProfile = useMemo(() => (
    translationProfilesQuery.data?.profiles.find(
      (item) => item.id === settings?.translationProfileId,
    ) ?? null
  ), [settings?.translationProfileId, translationProfilesQuery.data?.profiles]);

  const activeTranslationProfileRevision = useMemo(
    () => translationProfileRevision(activeTranslationProfile),
    [activeTranslationProfile],
  );

  const translationRequestTimeoutMs = useMemo(() => {
    const seconds = Math.max(5, Math.min(
      180, Number(activeTranslationProfile?.timeout_seconds ?? 60),
    ));
    return seconds * 1000 + 5000;
  }, [activeTranslationProfile?.timeout_seconds]);

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
    renderer.setAttribute('background', READER_THEME[next.theme].background);
    renderer.setStyles?.(readerCss(next, compactViewport));
  }, []);

  const updateSettings = useCallback((patch: Partial<ReaderSettings>) => {
    setSettings((current) => {
      if (!current) return current;
      const next = { ...current, ...patch };
      if (patch.translationEnabled === false && patch.translationView === undefined) {
        next.translationView = 'original';
      }
      settingsRef.current = next;
      applySettings(next);

      const { translationEnabled, translationView, ...globalPatch } = patch;
      if (Object.keys(globalPatch).length) saveSettings.mutate(globalPatch);
      const bookPatch: {
        translationEnabled?: boolean;
        translationView?: 'original' | 'translated';
      } = {};
      if (translationEnabled !== undefined) bookPatch.translationEnabled = translationEnabled;
      if (translationView !== undefined) bookPatch.translationView = translationView;
      else if (translationEnabled === false) bookPatch.translationView = 'original';
      if (Object.keys(bookPatch).length) saveBookState.mutate(bookPatch);
      return next;
    });
  }, [applySettings, saveBookState, saveSettings]);

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
      && !!currentSettings.translationProfileId;
    const currentConfigMatches = !!currentSettings
      && currentSettings.translationProfileId === job.settings.translationProfileId
      && activeTranslationProfileRevision === job.profileRevision
      && currentSettings.translationSourceLanguage === job.settings.translationSourceLanguage
      && currentSettings.translationTargetLanguage === job.settings.translationTargetLanguage
      && currentSettings.translationMode === job.settings.translationMode
      && currentSettings.translationPrompt === job.settings.translationPrompt
      && currentSettings.flow === job.settings.flow
      && currentSettings.font === job.settings.font
      && currentSettings.fontSize === job.settings.fontSize
      && currentSettings.lineHeight === job.settings.lineHeight
      && currentSettings.margin === job.settings.margin
      && currentSettings.maxColumnCount === job.settings.maxColumnCount
      && currentSettings.maxInlineSize === job.settings.maxInlineSize
      && currentSettings.spread === job.settings.spread;
    if (!enabled || !currentConfigMatches || translationCacheRef.current.has(job.key)
        || translationPreloadInFlightKeyRef.current === job.key) return;

    if (translationPreloadInFlightKeyRef.current) {
      translationPreloadQueuedRef.current = job;
      return;
    }

    const exactKey = translationPageKey(job.settings, job.blocks, job.profileRevision);
    if (translationCacheRef.current.has(exactKey)) return;

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
      mode: job.settings.translationMode,
      prompt: job.settings.translationPrompt,
      cache_enabled: true,
      blocks: translationRequestBlocks(job.settings.translationMode, job.blocks),
    }, controller.signal).then((response) => (
      translationResponseForMode(job.settings.translationMode, job.blocks, response)
    ));
    translationPreloadTaskRef.current = { key: job.key, exactKey, controller, promise };
    void promise.then((response) => {
      if (!controller.signal.aborted && !response.skipped) {
        const alignedBlocks = alignTranslatedBlocks(job.blocks, response.blocks);
        cacheTranslatedPage(job.key, alignedBlocks);
        cacheTranslatedPage(exactKey, alignedBlocks);
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
  }, [activeTranslationProfileRevision, bookLanguage, cacheTranslatedPage, fmt, id]);

  useEffect(() => {
    translationPreloadRunnerRef.current = runTranslationPreload;
  }, [runTranslationPreload]);

  const scheduleNextTranslationPreload = useCallback((activeSettings: ReaderSettings) => {
    if (!activeSettings.translationEnabled
        || activeSettings.translationView !== 'translated'
        || !activeSettings.translationCacheEnabled
        || !activeSettings.translationPreloadNextPage
        || !activeSettings.translationProfileId) return;

    const renderer = viewRef.current?.renderer;
    const extraction = extractVisiblePage(renderer, 1);
    const pageId = translationSourcePageId(renderer, 1);
    const key = translationPageCacheKey(
      activeSettings, activeTranslationProfileRevision, pageId,
    );
    if (!extraction || !key) return;

    let blocks = applySentenceCarry(
      extraction.blocks,
      pageId ? translationSentenceCarryRef.current.get(pageId) : undefined,
    );
    const following = extractVisiblePage(renderer, 2);
    if (following) {
      const split = splitTrailingSentenceForNext(blocks, following.blocks);
      blocks = split.blocks;
      const followingPageId = translationSourcePageId(renderer, 2);
      if (split.carry && followingPageId) {
        translationSentenceCarryRef.current.set(followingPageId, split.carry);
      }
    }

    if (!blocks.length || sourceAlreadyMatchesTarget(activeSettings, bookLanguage, blocks)) return;
    if (translationCacheRef.current.has(key)
        || translationPreloadInFlightKeyRef.current === key) return;
    translationPreloadRunnerRef.current({
      key,
      settings: { ...activeSettings },
      profileRevision: activeTranslationProfileRevision,
      blocks,
    });
  }, [activeTranslationProfileRevision, bookLanguage]);

  useEffect(() => {
    // Any translation configuration change invalidates an active speculative
    // request. The next completed current page will schedule a fresh preload.
    cancelTranslationPreload();
  }, [activeTranslationProfileRevision, cancelTranslationPreload,
    settings?.flow, settings?.font, settings?.fontSize, settings?.lineHeight,
    settings?.margin, settings?.maxColumnCount, settings?.maxInlineSize, settings?.spread,
    settings?.translationCacheEnabled, settings?.translationEnabled,
    settings?.translationPreloadNextPage, settings?.translationProfileId,
    settings?.translationMode, settings?.translationPrompt, settings?.translationSourceLanguage,
    settings?.translationTargetLanguage, settings?.translationView]);

  useEffect(() => {
    translationSentenceCarryRef.current.clear();
  }, [settings?.flow, settings?.font, settings?.fontSize, settings?.lineHeight,
    settings?.margin, settings?.maxColumnCount, settings?.maxInlineSize, settings?.spread]);

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

    const distance = scrolledPageTurnDistance(view.renderer);
    if (distance !== undefined) {
      const rtl = view.book?.dir === 'rtl';
      const logical = action === 'left'
        ? (rtl ? 'next' : 'prev')
        : action === 'right'
          ? (rtl ? 'prev' : 'next')
          : action;
      if (logical === 'prev') await view.prev(distance);
      else await view.next(distance);
      return;
    }

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

    if (currentSettings.flow === 'scrolled') {
      const overlay = translationOverlayRef.current;
      if (overlay) {
        const maxScroll = Math.max(0, overlay.scrollHeight - overlay.clientHeight);
        const step = Math.max(120, overlay.clientHeight * 0.88);
        if (direction === 'next' && overlay.scrollTop < maxScroll - 2) {
          overlay.scrollBy({ top: step, behavior: 'smooth' });
          return true;
        }
        if (direction === 'prev' && overlay.scrollTop > 2) {
          overlay.scrollBy({ top: -step, behavior: 'smooth' });
          return true;
        }
      }
    } else {
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
    }

    let nextPageReady = false;
    if (direction === 'next' && currentSettings.translationCacheEnabled) {
      const renderer = viewRef.current?.renderer;
      const nextPageId = translationSourcePageId(renderer, 1);
      const nextPageCacheKey = translationPageCacheKey(
        currentSettings, activeTranslationProfileRevision, nextPageId,
      );
      nextPageReady = !!nextPageCacheKey && translationCacheRef.current.has(nextPageCacheKey);
    }

    translationLandingRef.current = direction === 'next' ? 'first' : 'last';
    translationTransitionRef.current = true;
    setTranslationLoading(!nextPageReady);
    void navigate(direction).catch((cause) => {
      translationTransitionRef.current = false;
      translationLandingRef.current = null;
      setTranslationLoading(false);
      setTranslationError(cause instanceof Error ? cause.message : t('Page translation failed.'));
    });
    return true;
  }, [activeTranslationProfileRevision, navigate, showTranslationPage, t]);

  const navigateReader = useCallback((action: 'prev' | 'next' | 'left' | 'right') => {
    markReadingMovement();
    const direction = action === 'prev' || action === 'next'
      ? action
      : action === 'left'
        ? (bookRtl ? 'next' : 'prev')
        : (bookRtl ? 'prev' : 'next');
    if (navigateTranslation(direction)) return;
    void navigate(action);
  }, [bookRtl, markReadingMovement, navigate, navigateTranslation]);

  const navigateReaderOrClosePanel = useCallback((action: 'prev' | 'next' | 'left' | 'right') => {
    if (panel) {
      setPanel(null);
      return;
    }
    navigateReader(action);
  }, [navigateReader, panel]);

  const handleReaderWheel = useCallback((event: WheelEvent) => {
    const currentSettings = settingsRef.current;
    if (!currentSettings) return;
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    if (!allowsWheelPageTurn(event.target) || event.deltaY === 0) return;
    markReadingMovement();

    if (currentSettings.flow === 'scrolled') {
      const overlay = translationOverlayRef.current;
      const translationActive = currentSettings.translationEnabled
        && currentSettings.translationView === 'translated'
        && !!overlay && translationBlocksRef.current.length > 0;
      if (!translationActive) return;
      const maxScroll = Math.max(0, overlay.scrollHeight - overlay.clientHeight);
      const canScrollInside = event.deltaY > 0
        ? overlay.scrollTop < maxScroll - 2
        : overlay.scrollTop > 2;
      if (canScrollInside) return;
    }

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
  }, [markReadingMovement, navigateReader]);

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
      const renderer = viewRef.current?.renderer;
      const extraction = extractVisiblePage(renderer);
      if (!extraction) {
        // A translated-page boundary can land on an image/blank page. That
        // still completes the page turn: never leave the translation transition
        // armed, otherwise stale translated blocks make navigateTranslation()
        // consume every later page-turn without moving Foliate.
        cancelTranslationPreload();
        translationAbortRef.current?.abort();
        translationAbortRef.current = null;
        translationInFlightKeyRef.current = null;
        translationCurrentKeyRef.current = null;
        translationTransitionRef.current = false;
        translationLandingRef.current = null;
        setTranslationBlocks([]);
        setTranslationSegments([]);
        setTranslationLayout(null);
        showTranslationPage(0);
        setTranslationLoading(false);
        setTranslationSkipped(false);
        setTranslationError(t('No visible text was found on this page.'));
        return;
      }
      const { styles: sourceStyles, sourceRuns, segments, layout } = extraction;
      const pageId = translationSourcePageId(renderer);
      const pageCacheKey = translationPageCacheKey(
        settings, activeTranslationProfileRevision, pageId,
      );
      let blocks = applySentenceCarry(
        extraction.blocks,
        pageId ? translationSentenceCarryRef.current.get(pageId) : undefined,
      );
      const nextExtraction = extractVisiblePage(renderer, 1);
      if (nextExtraction) {
        const split = splitTrailingSentenceForNext(blocks, nextExtraction.blocks);
        blocks = split.blocks;
        const nextPageId = translationSourcePageId(renderer, 1);
        if (split.carry && nextPageId) {
          translationSentenceCarryRef.current.set(nextPageId, split.carry);
        }
      }
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

      const key = translationPageKey(settings, blocks, activeTranslationProfileRevision);
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
        const alignedBlocks = alignTranslatedBlocks(blocks, response.blocks);
        if (settings.translationCacheEnabled) {
          cacheTranslatedPage(key, alignedBlocks);
          if (pageCacheKey) cacheTranslatedPage(pageCacheKey, alignedBlocks);
        }
        setTranslationLayout(layout);
        setTranslationSegments(segments);
        setTranslationBlocks(styled(alignedBlocks));
        translationTransitionRef.current = false;
        scheduleNextTranslationPreload(settings);
      };
      setTranslationSkipped(false);
      const exactLocal = settings.translationCacheEnabled
        ? translationCacheRef.current.get(key)
        : undefined;
      const pageLocal = settings.translationCacheEnabled && pageCacheKey
        ? translationCacheRef.current.get(pageCacheKey)
        : undefined;
      const local = exactLocal ?? (pageLocal ? alignTranslatedBlocks(blocks, pageLocal) : undefined);
      if (local) {
        if (!exactLocal && settings.translationCacheEnabled) cacheTranslatedPage(key, local);
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
      const preloadMatchesCurrent = !!preloadTask
        && (preloadTask.key === pageCacheKey || preloadTask.exactKey === key);
      if (preloadTask && !preloadMatchesCurrent) {
        cancelTranslationPreload();
      }
      if (preloadMatchesCurrent && preloadTask) {
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
          mode: settings.translationMode,
          prompt: settings.translationPrompt,
          cache_enabled: settings.translationCacheEnabled,
          blocks: translationRequestBlocks(settings.translationMode, blocks),
        }, controller.signal);
        if (controller.signal.aborted) return;
        applyTranslationResponse(translationResponseForMode(
          settings.translationMode, blocks, response,
        ));
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
  }, [activeTranslationProfileRevision, bookLanguage, fmt, id,
    location.cfi, location.fraction, ready,
    settings?.flow, settings?.font, settings?.fontSize, settings?.lineHeight,
    settings?.margin, settings?.maxColumnCount, settings?.maxInlineSize, settings?.spread,
    settings?.translationEnabled, settings?.translationProfileId,
    settings?.translationCacheEnabled, settings?.translationPreloadNextPage,
    settings?.translationMode, settings?.translationPrompt, settings?.translationSourceLanguage,
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
      const landing = translationLandingRef.current;
      if (settings?.flow === 'scrolled') {
        translationPageCountRef.current = 1;
        showTranslationPage(0);
        const overlay = translationOverlayRef.current;
        if (overlay && landing) {
          overlay.scrollTop = landing === 'last'
            ? Math.max(0, overlay.scrollHeight - overlay.clientHeight)
            : 0;
        }
        translationLandingRef.current = null;
        return;
      }
      const step = Math.max(1, translationLayout.contentWidth + translationLayout.columnGap);
      const count = Math.max(1, Math.ceil(
        (content.scrollWidth + translationLayout.columnGap - 0.5) / step,
      ));
      translationPageCountRef.current = count;
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
  }, [settings?.flow, showTranslationPage, translationBlocks, translationLayout, translationSegments]);

  useEffect(() => {
    if (!selectedFormat || !settingsQuery.data || !bookStateQuery.data
        || bookStateQuery.isFetching || !positionQuery.isFetched || !hostRef.current) return;
    let cancelled = false;
    let restoringInitialPosition = true;
    resetReadingMovement();
    const host = hostRef.current;
    const view = createFoliateView(styles.foliateView);
    viewRef.current = view;
    host.replaceChildren(view);
    annotationsRef.current.clear();
    setAnnotations([]);
    setAnnotationEditor(null);
    setReady(false);
    setError(null);
    setBookLanguage('');
    setBookRtl(false);
    const initialSettings: ReaderSettings = {
      ...settingsQuery.data.reader,
      translationEnabled: bookStateQuery.data.translationEnabled,
      translationView: bookStateQuery.data.translationView,
    };
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
      if (detail.cfi && !restoringInitialPosition && readingMovementRef.current) {
        const anchorText = detail.range?.toString().replace(/\s+/gu, ' ').trim().slice(0, 1000);
        schedulePosition(detail.cfi, detail.fraction ?? 0, anchorText || undefined);
      }
    };

    const attachSelection = (doc: Document, index: number) => {
      let selectionTimer: number | null = null;
      const readSelection = (dismissCollapsed: boolean) => {
        if (cancelled) return;
        const selection = doc.getSelection();
        if (!selection || selection.isCollapsed || !selection.rangeCount) {
          if (dismissCollapsed) dismissSelection();
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
      const scheduleSelectionRead = (dismissCollapsed: boolean, delay = 0) => {
        if (selectionTimer !== null) window.clearTimeout(selectionTimer);
        selectionTimer = window.setTimeout(() => {
          selectionTimer = null;
          readSelection(dismissCollapsed);
        }, delay);
      };
      doc.addEventListener('mouseup', () => scheduleSelectionRead(true));
      doc.addEventListener('keyup', () => scheduleSelectionRead(true));
      doc.addEventListener('touchend', () => scheduleSelectionRead(false, 120), { passive: true });
      doc.addEventListener('contextmenu', () => scheduleSelectionRead(false, 120));
      doc.addEventListener('selectionchange', () => scheduleSelectionRead(false, 80));
      const armMovement = markReadingMovement;
      const armPointerDrag = (event: PointerEvent) => {
        if (event.pointerType === 'touch' || event.buttons !== 0) armMovement();
      };
      const armLinkNavigation = (event: MouseEvent) => {
        if (event.target instanceof Element && event.target.closest('a[href]')) armMovement();
      };
      doc.addEventListener('pointermove', armPointerDrag, { passive: true });
      doc.addEventListener('touchmove', armMovement, { passive: true });
      doc.addEventListener('wheel', armMovement, { passive: true });
      doc.addEventListener('keydown', armMovement);
      doc.addEventListener('click', armLinkNavigation, { capture: true, passive: true });
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
      draw(drawFoliateHighlight, { color: annotation.color ?? 'yellow', hasNote: !!annotation.note });
    };
    const onShowAnnotation = (event: Event) => {
      const value = (event as CustomEvent<{ value: string }>).detail.value;
      const annotation = annotationsRef.current.get(value);
      if (annotation) openEditAnnotation(annotation);
    };
    const redrawAnnotations = () => {
      for (const annotation of annotationsRef.current.values()) {
        if (!annotation.unanchored) void view.addAnnotation(annotation);
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
        const annotationPromise = apiGet<{ annotations: ServerAnnotation[]; devices?: Record<string, { label?: string }> }>(
          `/annotations/${id}/data.json?format=${encodeURIComponent(fmt)}`,
        ).catch(() => ({ annotations: [], devices: {} }));
        const response = await fetch(resourceUrl(`/show/${id}/${formatName.toLowerCase()}`), {
          credentials: 'include',
        });
        if (!response.ok) throw new Error(t('Could not load the book file ({status})', { status: response.status }));
        const data = await response.arrayBuffer();
        if (cancelled) return;
        await view.open(new File([data], readerFileName(formatName), { type: readerMime(formatName) }));
        if (cancelled) return;
        applySettings(initialSettings);
        setBookRtl(view.book?.dir === 'rtl');
        setBookLanguage((current) => normalizeLanguageCode(view.book?.metadata?.language) || current);
        setTitle(formatLanguageMap(view.book?.metadata?.title) || bookQuery.data?.title || t('Untitled'));
        setToc(flattenToc(view.book?.toc ?? []));
        setSectionFractions(view.getSectionFractions());


        const savedLocator = positionQuery.data?.bookmark;
        const legacyFb2Fraction = fmt === 'fb2' ? parseFb2ScrollBookmark(savedLocator) : null;
        const savedFraction = Number(positionQuery.data?.position_fraction ?? legacyFb2Fraction ?? 0);
        const moonAnchor = positionQuery.data?.position_source === 'moonreader'
          ? positionQuery.data?.position_anchor?.trim()
          : '';
        const savedFoliateCfi = savedLocator?.startsWith('epubcfi(') ? savedLocator : undefined;
        const fallbackLocation = savedFoliateCfi
          ?? (savedFraction > 0 ? { fraction: Math.min(1, Math.max(0, savedFraction)) } : undefined);

        if (moonAnchor) {
          // Moon's percentage and Foliate's section-size fraction are different
          // coordinate systems. Initialize without that fraction, resolve the
          // Moon chapter/offset by visible text in Foliate's own DOM, then go to
          // the resulting native CFI. Suppress relocate writes during restore so
          // merely opening the book cannot push Foliate's fraction back to Moon.
          await view.init({ showTextStart: true });
          const moonCfi = await findMoonAnchorCfi(
            view, moonAnchor,
            positionQuery.data?.position_section ?? positionQuery.data?.position_chapter,
          );
          if (moonCfi) await view.goTo(moonCfi);
          else if (savedFraction > 0) await view.goToFraction(Math.min(1, Math.max(0, savedFraction)));
          else if (savedLocator) await view.goTo(savedLocator);
        } else {
          await view.init({ lastLocation: fallbackLocation, showTextStart: true });
        }
        if (cancelled) return;
        restoringInitialPosition = false;
        resetReadingMovement();
        setReady(true);
        void annotationPromise.then((annotationPayload) => {
          if (cancelled) return;
          const deviceMap: Record<string, { label?: string }> = annotationPayload.devices ?? {};
          const loaded = annotationPayload.annotations.map((row): FoliateAnnotation => {
            const unanchored = row.position_type === 'unanchored';
            const deviceLabel = row.origin_device_id
              ? deviceMap[row.origin_device_id]?.label
              : undefined;
            const sourceLabel = deviceLabel
              || (row.source && row.source !== 'webreader' ? row.source : undefined);
            return {
              value: row.cfi_range ?? `unanchored:${row.annotation_id}`,
              color: annotationColor(row.highlight_color),
              note: row.note_text, id: row.annotation_id, text: row.highlighted_text, unanchored, sourceLabel,
            };
          });
          for (const annotation of loaded) annotationsRef.current.set(annotation.value, annotation);
          setAnnotations(Array.from(annotationsRef.current.values()));
          for (const annotation of loaded) {
            if (!annotation.unanchored) void view.addAnnotation(annotation);
          }
        });
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
    positionQuery.data?.position_fraction, positionQuery.data?.position_source,
    positionQuery.data?.position_anchor, positionQuery.data?.position_chapter,
    positionQuery.data?.position_section,
    positionQuery.isFetched, selectedFormat,
    settingsQuery.data, bookStateQuery.data, bookStateQuery.isFetching,
    schedulePosition, dismissSelection, handleReaderWheel,
    markReadingMovement, openEditAnnotation, resetReadingMovement, restoreInlineTranslations, t]);
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
  const upsertAnnotation = useCallback((annotation: FoliateAnnotation) => {
    annotationsRef.current.set(annotation.value, annotation);
    setAnnotations(Array.from(annotationsRef.current.values()));
  }, []);

  const openCreateHighlight = useCallback((focusNote: boolean) => {
    if (!pendingSelection) return;
    setAnnotationEditor({
      mode: 'create',
      selection: { value: pendingSelection.value, text: pendingSelection.text },
      color: 'yellow', note: '', focusNote,
    });
  }, [pendingSelection]);

  const openStandaloneNote = useCallback(() => {
    setAnnotationEditor({ mode: 'standalone', color: 'yellow', note: '', focusNote: true });
  }, []);



  const saveAnnotationEditor = useCallback(async (color: HighlightColor, note: string) => {
    const editor = annotationEditor;
    if (!editor) return;
    if (editor.mode === 'create') {
      const row = await apiPost<ServerAnnotation>(`/annotations/${id}`, {
        cfi_range: editor.selection.value,
        highlighted_text: editor.selection.text,
        highlight_color: color,
        note_text: note || null,
        format: fmt.toUpperCase(),
      });
      const annotation: FoliateAnnotation = {
        value: row.cfi_range ?? editor.selection.value,
        color: annotationColor(row.highlight_color ?? color),
        note: row.note_text,
        id: row.annotation_id,
        text: row.highlighted_text,
      };
      upsertAnnotation(annotation);
      await viewRef.current?.addAnnotation(annotation);
      dismissSelection();
    } else if (editor.mode === 'standalone') {
      const existing = editor.annotation;
      if (existing?.id) {
        const row = await apiPatch<ServerAnnotation>(
          `/annotations/${id}/${encodeURIComponent(existing.id)}`,
          { note_text: note || null, format: fmt.toUpperCase() },
        );
        upsertAnnotation({ ...existing, note: row.note_text });
      } else {
        const row = await apiPost<ServerAnnotation>(`/annotations/${id}`, {
          position_type: 'unanchored',
          note_text: note,
          chapter_progress: currentRef.current.fraction ?? 0,
          format: fmt.toUpperCase(),
        });
        upsertAnnotation({
          value: `unanchored:${row.annotation_id}`,
          color: 'yellow', note: row.note_text ?? note,
          id: row.annotation_id, text: null, unanchored: true,
        });
      }
    } else {
      const existing = editor.annotation;
      if (!existing.id) return;
      const row = await apiPatch<ServerAnnotation>(
        `/annotations/${id}/${encodeURIComponent(existing.id)}`,
        { highlight_color: color, note_text: note || null, format: fmt.toUpperCase() },
      );
      const updated: FoliateAnnotation = {
        ...existing,
        color: annotationColor(row.highlight_color ?? color),
        note: row.note_text,
      };
      await viewRef.current?.deleteAnnotation(existing);
      upsertAnnotation(updated);
      await viewRef.current?.addAnnotation(updated);
    }
    setAnnotationEditor(null);
  }, [annotationEditor, dismissSelection, fmt, id, upsertAnnotation]);

  const removeAnnotation = useCallback(async (annotation: FoliateAnnotation) => {
    if (!annotation.id) return;
    await apiDelete(`/annotations/${id}/${encodeURIComponent(annotation.id)}?format=${encodeURIComponent(fmt)}`);
    annotationsRef.current.delete(annotation.value);
    setAnnotations(Array.from(annotationsRef.current.values()));
    if (!annotation.unanchored) await viewRef.current?.deleteAnnotation(annotation);
    setAnnotationEditor((current) => {
      if (!current || current.mode === 'create') return current;
      const currentId = current.mode === 'edit' ? current.annotation.id : current.annotation?.id;
      return currentId === annotation.id ? null : current;
    });
  }, [fmt, id]);

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
        mode: 'simple',
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
    markReadingMovement();
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

  const progress = Math.max(0, Math.min(1, location.fraction ?? 0));
  const serverProgress = Number(savedPositionFraction ?? positionQuery.data?.position_fraction);
  const canonicalProgress = Number.isFinite(serverProgress)
    ? Math.max(0, Math.min(1, serverProgress))
    : progress;
  const percent = formatReadingProgress(canonicalProgress * 100);
  const leftPageLabel = bookRtl ? t('Next page') : t('Previous page');
  const rightPageLabel = bookRtl ? t('Previous page') : t('Next page');
  const translationRequested = !!settings?.translationEnabled
    && settings.translationView === 'translated'
    && !!settings.translationProfileId;
  const translationOverlayVisible = translationRequested
    && !translationSkipped && !!translationLayout && translationSegments.length > 0;
  const translationActivity = translationRequested && !translationError
    && (translationLoading || translationPreloading);
  const bookmarks = bookmarksQuery.data?.bookmarks ?? [];
  const loading = bookQuery.isLoading || settingsQuery.isLoading
    || bookStateQuery.isLoading || bookStateQuery.isFetching || positionQuery.isLoading;

  if (loading) return <SpinnerCentered size={44} />;
  if (bookQuery.error) return <EmptyState message={t('Could not load the book.')} />;
  if (!selectedFormat) return <EmptyState message={t('No supported reader format is available.')} />;
  return (
    <main ref={shellRef} className={`${styles.reader} ${styles[settings?.theme ?? 'lightTheme']}`}>
      <ReaderToolbar
        bookId={id} title={title || bookQuery.data?.title || t('Untitled')} format={selectedFormat.format}
        panel={panel} setPanel={setPanel} canBookmark={!!location.cfi && !createBookmark.isPending}
        addBookmark={addReaderBookmark} speaking={speaking} toggleSpeech={toggleSpeech}
        settings={settings} updateSettings={updateSettings}
        translationSkipped={translationSkipped} translationActivity={translationActivity}
        translationLoading={translationLoading} translationPreloading={translationPreloading}
        fullscreenSupported={fullscreenSupported} isFullscreen={isFullscreen} toggleFullscreen={toggleFullscreen}
      />

      {pendingSelection && <SelectionActions
        text={pendingSelection.text} openHighlight={openCreateHighlight}
        translate={() => void translateSelectedText()} translationLoading={selectionTranslationLoading}
        openChatGpt={openSelectedTextInChatGpt} dismiss={dismissSelection} error={selectionTranslationError}
      />}

      {annotationEditor && (
        <AnnotationComposer
          key={annotationEditor.mode === 'edit' ? annotationEditor.annotation.id :
            annotationEditor.mode === 'standalone' ? annotationEditor.annotation?.id ?? 'new-note' :
              `new-${annotationEditor.selection.value}`}
          state={annotationEditor}
          onClose={closeAnnotationEditor}
          onSave={saveAnnotationEditor}
          onDelete={annotationEditor.mode === 'edit'
            ? () => removeAnnotation(annotationEditor.annotation)
            : annotationEditor.mode === 'standalone' && annotationEditor.annotation
              ? () => removeAnnotation(annotationEditor.annotation!)
              : undefined}
        />
      )}

      <div className={styles.workspace}>
        {panel && (
          <button
            type="button"
            className={styles.sidePanelBackdrop}
            onClick={closePanel}
            tabIndex={-1}
            aria-label={t('Close')}
            title={t('Close')}
          />
        )}
        {panel && <ReaderSidePanel
          panel={panel} onClose={closePanel} toc={toc}
          onNavigate={(target) => { markReadingMovement(); restoreInlineTranslations(); dismissSelection(); void viewRef.current?.goTo(target); closePanel(); }}
          searchText={searchText} setSearchText={setSearchText} runSearch={() => void runSearch()}
          searching={searching} searchProgress={searchProgress} searchResults={searchResults}
          bookmarks={bookmarks} openBookmark={openReaderBookmark}
          deleteBookmark={(bookmarkId) => deleteBookmark.mutate(bookmarkId)}
          annotations={annotations}
          createStandaloneNote={openStandaloneNote}
          showAnnotation={(annotation) => {
            if (annotation.unanchored) return;
            // A jump to a highlight is inspection, not reading progress. Clear
            // any previously armed movement before Foliate emits relocate.
            resetReadingMovement();
            restoreInlineTranslations();
            void viewRef.current?.showAnnotation(annotation);
            closePanel();
          }}
          editAnnotation={openEditAnnotation}
          removeAnnotation={(annotation) => void removeAnnotation(annotation)}
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
            <TranslationOverlay settings={settings} layout={translationLayout} segments={translationSegments}
              blocks={translationBlocks} pageIndex={translationPageIndex}
              overlayRef={translationOverlayRef} pagerContentRef={translationPagerContentRef} />
          )}

          {settings?.tapToTurn && settings.flow === 'paginated' && ready && !error && (
            <>
              <button className={`${styles.tapZone} ${styles.tapZoneLeft} ${
                translationOverlayVisible ? styles.translationTapZone : ''
              }`}
                data-reader-wheel-page-zone
                onClick={(event) => { navigateReaderOrClosePanel('left'); event.currentTarget.blur(); }}
                title={leftPageLabel} aria-label={leftPageLabel}>
                <ChevronLeft size={30} aria-hidden="true" />
              </button>
              <button className={`${styles.tapZone} ${styles.tapZoneRight} ${
                translationOverlayVisible ? styles.translationTapZone : ''
              }`}
                data-reader-wheel-page-zone
                onClick={(event) => { navigateReaderOrClosePanel('right'); event.currentTarget.blur(); }}
                title={rightPageLabel} aria-label={rightPageLabel}>
                <ChevronRight size={30} aria-hidden="true" />
              </button>
            </>
          )}
        </section>
      </div>
      <ReaderBottomBar location={location} percent={percent} progress={progress}
        sectionFractions={sectionFractions}
        previous={() => navigateReaderOrClosePanel('prev')}
        next={() => navigateReaderOrClosePanel('next')}
        changeProgress={(fraction) => {
          markReadingMovement();
          restoreInlineTranslations();
          dismissSelection();
          setLocation((current) => ({ ...current, fraction }));
          void viewRef.current?.goToFraction(fraction);
        }}
      />
      {saveError && <div className={styles.saveError} role="alert">
        {t('Could not save reading position. It will be retried automatically.')}
      </div>}
    </main>
  );
}
