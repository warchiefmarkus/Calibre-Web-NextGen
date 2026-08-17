import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'wouter';
import {
  AlertTriangle, Check, Download, ExternalLink, LoaderCircle, StickyNote, Trash2, X,
} from 'lucide-react';
import {
  PDFViewer,
  type PDFViewerConfig,
  type PluginRegistry,
} from '@embedpdf/react-pdf-viewer';
import {
  ANNOTATION_PLUGIN_ID,
  type AnnotationCapability,
  type AnnotationEvent,
  type AnnotationTransferItem,
} from '@embedpdf/plugin-annotation';
import {
  SCROLL_PLUGIN_ID,
  type ScrollCapability,
  type ScrollMetrics,
} from '@embedpdf/plugin-scroll';
import { PdfAnnotationSubtypeName, type PdfAnnotationObject } from '@embedpdf/models';
import { apiDelete, apiGet, apiPatch, apiPost, resourceUrl } from '../lib/api';
import { useBookmark } from '../lib/queries';
import { useReadingPositionSaver } from '../lib/readerProgress';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import { VisuallyHidden } from '../components/VisuallyHidden';
import { useT } from '../lib/i18n';
import styles from './PdfReader.module.css';

type ServerPdfAnnotation = {
  annotation_id?: string;
  id?: string;
  highlighted_text?: string | null;
  highlight_color?: string | null;
  note_text?: string | null;
  pdf_page?: number | null;
  pdf_quad?: unknown;
  pdf_quad_json?: unknown;
};

type PdfPosition = {
  type: 'pdf-position';
  version: 1;
  page: number;
  scroll?: { x: number; y: number };
};

type EncodedBinary = {
  __embedpdfBinary: true;
  data: string;
};

function encodeBytes(bytes: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

function decodeBytes(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function serializableTransferItem(item: AnnotationTransferItem): unknown {
  return JSON.parse(JSON.stringify(item, (_key, value: unknown) => {
    if (value instanceof ArrayBuffer) {
      return { __embedpdfBinary: true, data: encodeBytes(new Uint8Array(value)) } satisfies EncodedBinary;
    }
    if (ArrayBuffer.isView(value)) {
      const view = value as ArrayBufferView;
      return {
        __embedpdfBinary: true,
        data: encodeBytes(new Uint8Array(view.buffer, view.byteOffset, view.byteLength)),
      } satisfies EncodedBinary;
    }
    return value;
  }));
}

function reviveTransferItem(value: unknown): AnnotationTransferItem | null {
  try {
    const parsed = typeof value === 'string' ? JSON.parse(value) : value;
    const json = JSON.stringify(parsed);
    const revived = JSON.parse(json, (key, item: unknown) => {
      if (item && typeof item === 'object' && (item as Partial<EncodedBinary>).__embedpdfBinary === true) {
        return decodeBytes(String((item as EncodedBinary).data)).buffer;
      }
      if ((key === 'created' || key === 'modified') && typeof item === 'string') {
        const date = new Date(item);
        return Number.isNaN(date.getTime()) ? item : date;
      }
      return item;
    }) as AnnotationTransferItem;
    if (!revived?.annotation || typeof revived.annotation.id !== 'string') return null;
    if (!Number.isInteger(revived.annotation.pageIndex) || revived.annotation.pageIndex < 0) return null;
    return revived;
  } catch {
    return null;
  }
}

function parsePosition(bookmark: string | null | undefined): PdfPosition | null {
  if (!bookmark) return null;
  try {
    const value = JSON.parse(bookmark) as Partial<PdfPosition>;
    if (value.type !== 'pdf-position' || value.version !== 1) return null;
    const page = Number(value.page);
    if (!Number.isInteger(page) || page < 1) return null;
    return {
      type: 'pdf-position',
      version: 1,
      page,
      scroll: value.scroll && Number.isFinite(value.scroll.x) && Number.isFinite(value.scroll.y)
        ? { x: value.scroll.x, y: value.scroll.y }
        : undefined,
    };
  } catch {
    return null;
  }
}

function positionBookmark(metrics: ScrollMetrics): string {
  const value: PdfPosition = {
    type: 'pdf-position',
    version: 1,
    page: Math.max(1, Math.trunc(metrics.currentPage)),
    scroll: {
      x: Math.round(metrics.scrollOffset.x),
      y: Math.round(metrics.scrollOffset.y),
    },
  };
  return JSON.stringify(value);
}

function annotationNote(annotation: PdfAnnotationObject): string | null {
  return typeof annotation.contents === 'string' ? annotation.contents : null;
}

function annotationTypeLabel(annotation: PdfAnnotationObject): string {
  const raw = PdfAnnotationSubtypeName[annotation.type] ?? String(annotation.type);
  return raw.toLowerCase().replace(/(^|[_-])(\w)/g, (_match, _separator, letter: string) => letter.toUpperCase());
}

function serverAnnotationId(row: ServerPdfAnnotation): string | null {
  const value = row.annotation_id ?? row.id;
  return typeof value === 'string' && value.trim() ? value : null;
}

function serverAnnotationTransfer(row: ServerPdfAnnotation): AnnotationTransferItem | null {
  return reviveTransferItem(row.pdf_quad ?? row.pdf_quad_json);
}

function serverAnnotationEmbedId(row: ServerPdfAnnotation): string | null {
  return serverAnnotationTransfer(row)?.annotation.id ?? null;
}

function appPdfTheme(): 'light' | 'dark' {
  if (typeof document === 'undefined') return 'dark';
  const theme = document.documentElement.getAttribute('data-theme');
  return theme === 'light' || theme === 'sepia' ? 'light' : 'dark';
}

export function PdfReader({ id, format }: { id: string; format: string }) {
  const t = useT();
  const fmt = format.toLowerCase();
  const src = useMemo(() => resourceUrl(`/show/${id}/${fmt}`), [fmt, id]);
  const downloadUrl = useMemo(() => resourceUrl(`/download/${id}/${fmt}`), [fmt, id]);
  const wasmUrl = useMemo(() => resourceUrl('/static/app/embedpdf/pdfium.wasm'), []);
  const positionQuery = useBookmark(id, fmt);
  const { schedule: schedulePosition, saveError, saveState } = useReadingPositionSaver(id, fmt, 1000);
  const annotationCapabilityRef = useRef<AnnotationCapability | null>(null);
  const scrollCapabilityRef = useRef<ScrollCapability | null>(null);
  const subscriptionsRef = useRef<Array<() => void>>([]);
  const mutationQueueRef = useRef(new Map<string, Promise<void>>());
  const serverIdByEmbedIdRef = useRef(new Map<string, string>());
  const contextByEmbedIdRef = useRef(new Map<string, AnnotationTransferItem['ctx']>());
  const importedAnnotationIdsRef = useRef(new Set<string>());
  const hydratingRef = useRef(true);
  const hydratedRef = useRef(false);
  const [sourceReady, setSourceReady] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [viewerReady, setViewerReady] = useState(false);
  const [layoutReady, setLayoutReady] = useState(false);
  const [serverAnnotations, setServerAnnotations] = useState<ServerPdfAnnotation[] | null>(null);
  const [annotationSaveError, setAnnotationSaveError] = useState(false);
  const [pendingAnnotationSaves, setPendingAnnotationSaves] = useState(0);
  const [notesOpen, setNotesOpen] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  const config = useMemo<PDFViewerConfig>(() => ({
    src,
    wasmUrl,
    worker: false,
    log: true,
    fontFallback: null,
    fonts: { ui: null, signature: null },
    theme: { preference: appPdfTheme() },
    annotations: {
      autoCommit: true,
      deactivateToolAfterCreate: false,
      selectAfterCreate: true,
    },
    stamp: {
      defaultLibrary: false,
      manifests: [],
    },
  }), [src, wasmUrl]);

  useEffect(() => {
    const controller = new AbortController();
    setSourceReady(false);
    setSourceError(null);
    fetch(src, { method: 'HEAD', credentials: 'include', signal: controller.signal })
      .then((response) => {
        if (!response.ok && response.status !== 405) {
          throw new Error(t('Could not load the PDF file ({status})', { status: response.status }));
        }
        setSourceReady(true);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setSourceError(cause instanceof Error ? cause.message : t('Could not load this PDF file.'));
      });
    return () => controller.abort();
  }, [src, t]);

  useEffect(() => {
    let active = true;
    setServerAnnotations(null);
    apiGet<{ annotations: ServerPdfAnnotation[] }>(
      `/annotations/${id}/data.json?format=${encodeURIComponent(fmt)}`,
    ).then((payload) => {
      if (active) setServerAnnotations(payload.annotations ?? []);
    }).catch(() => {
      if (active) setServerAnnotations([]);
    });
    return () => { active = false; };
  }, [fmt, id]);

  const enqueueMutation = useCallback((annotationId: string, operation: () => Promise<void>) => {
    setPendingAnnotationSaves((count) => count + 1);
    const previous = mutationQueueRef.current.get(annotationId) ?? Promise.resolve();
    const next = previous
      .catch(() => undefined)
      .then(operation)
      .then(() => setAnnotationSaveError(false))
      .catch(() => setAnnotationSaveError(true));
    mutationQueueRef.current.set(annotationId, next);
    void next.finally(() => {
      if (mutationQueueRef.current.get(annotationId) === next) {
        mutationQueueRef.current.delete(annotationId);
      }
      setPendingAnnotationSaves((count) => Math.max(0, count - 1));
    });
  }, []);

  const persistAnnotation = useCallback((event: Exclude<AnnotationEvent, { type: 'loaded' }>) => {
    if (!event.committed) return;
    const embedId = event.annotation.id;
    const importedHydrationEvent = importedAnnotationIdsRef.current.delete(embedId);
    if (hydratingRef.current || importedHydrationEvent) return;
    const context = event.type === 'create'
      ? event.ctx
      : contextByEmbedIdRef.current.get(embedId);
    if (context !== undefined) contextByEmbedIdRef.current.set(embedId, context);
    const locator = serializableTransferItem({ annotation: event.annotation, ctx: context });
    const payload = {
      format: fmt,
      annotation_id: embedId,
      position_type: 'pdf_quad',
      pdf_page: event.pageIndex + 1,
      pdf_quad: locator,
      highlighted_text: null,
      note_text: annotationNote(event.annotation),
      highlight_color: 'yellow',
    };
    const routeId = serverIdByEmbedIdRef.current.get(embedId);

    setServerAnnotations((current) => {
      if (current === null) return current;
      if (event.type === 'delete') {
        return current.filter((row) => serverAnnotationId(row) !== routeId
          && serverAnnotationEmbedId(row) !== embedId);
      }
      const nextRow: ServerPdfAnnotation = {
        annotation_id: routeId ?? embedId,
        highlighted_text: null,
        highlight_color: 'yellow',
        note_text: payload.note_text,
        pdf_page: payload.pdf_page,
        pdf_quad: locator,
      };
      const index = current.findIndex((row) => serverAnnotationId(row) === routeId
        || serverAnnotationEmbedId(row) === embedId);
      if (index < 0) return [...current, nextRow];
      const next = [...current];
      next[index] = { ...next[index], ...nextRow };
      return next;
    });

    enqueueMutation(embedId, async () => {
      if (event.type === 'delete') {
        if (!routeId) return;
        await apiDelete(`/annotations/${id}/${encodeURIComponent(routeId)}?format=${encodeURIComponent(fmt)}`);
        serverIdByEmbedIdRef.current.delete(embedId);
        contextByEmbedIdRef.current.delete(embedId);
        return;
      }
      if (!routeId) {
        const created = await apiPost<ServerPdfAnnotation>(`/annotations/${id}`, payload);
        const createdId = serverAnnotationId(created) ?? embedId;
        serverIdByEmbedIdRef.current.set(embedId, createdId);
        setServerAnnotations((current) => current?.map((row) => (
          serverAnnotationEmbedId(row) === embedId
            ? { ...row, ...created, annotation_id: createdId, pdf_quad: locator }
            : row
        )) ?? current);
        return;
      }
      const updated = await apiPatch<ServerPdfAnnotation>(
        `/annotations/${id}/${encodeURIComponent(routeId)}?format=${encodeURIComponent(fmt)}`,
        payload,
      );
      setServerAnnotations((current) => current?.map((row) => (
        serverAnnotationId(row) === routeId
          ? { ...row, ...updated, annotation_id: routeId, pdf_quad: locator }
          : row
      )) ?? current);
    });
  }, [enqueueMutation, fmt, id]);

  const onReady = useCallback((registry: PluginRegistry) => {
    subscriptionsRef.current.splice(0).forEach((unsubscribe) => unsubscribe());
    const annotationPlugin = registry.getPlugin(ANNOTATION_PLUGIN_ID);
    const scrollPlugin = registry.getPlugin(SCROLL_PLUGIN_ID);
    const annotations = annotationPlugin?.provides?.() as AnnotationCapability | undefined;
    const scroll = scrollPlugin?.provides?.() as ScrollCapability | undefined;
    if (!annotations || !scroll) {
      setSourceError(t('The PDF reader could not initialize its navigation or annotation plugins.'));
      return;
    }
    annotationCapabilityRef.current = annotations;
    scrollCapabilityRef.current = scroll;
    subscriptionsRef.current.push(
      annotations.onAnnotationEvent((event) => {
        if (event.type !== 'loaded') persistAnnotation(event);
      }),
      scroll.onLayoutReady((event) => {
        if (event.isInitial) {
          setTotalPages(Math.max(1, scroll.getTotalPages()));
          setLayoutReady(true);
        }
      }),
      scroll.onScroll((event) => {
        const total = Math.max(1, scroll.getTotalPages());
        const page = Math.max(1, Math.min(total, event.metrics.currentPage));
        setTotalPages(total);
        setCurrentPage(page);
        if (!hydratedRef.current) return;
        const fraction = total === 1 ? 0 : (page - 1) / (total - 1);
        schedulePosition(positionBookmark(event.metrics), fraction);
      }),
    );
    setViewerReady(true);
  }, [persistAnnotation, schedulePosition, t]);

  useEffect(() => () => {
    subscriptionsRef.current.splice(0).forEach((unsubscribe) => unsubscribe());
    annotationCapabilityRef.current = null;
    scrollCapabilityRef.current = null;
  }, []);

  useEffect(() => {
    if (!viewerReady || !layoutReady || serverAnnotations === null || !positionQuery.isFetched) return;
    const annotations = annotationCapabilityRef.current;
    const scroll = scrollCapabilityRef.current;
    if (!annotations || !scroll || hydratedRef.current) return;

    hydratingRef.current = true;
    const transferItems: AnnotationTransferItem[] = [];
    for (const row of serverAnnotations) {
      const transfer = reviveTransferItem(row.pdf_quad ?? row.pdf_quad_json);
      const rowId = serverAnnotationId(row);
      if (!transfer || !rowId) continue;
      transferItems.push(transfer);
      importedAnnotationIdsRef.current.add(transfer.annotation.id);
      serverIdByEmbedIdRef.current.set(transfer.annotation.id, rowId);
      if (transfer.ctx !== undefined) contextByEmbedIdRef.current.set(transfer.annotation.id, transfer.ctx);
    }
    if (transferItems.length) annotations.importAnnotations(transferItems);

    const totalPages = Math.max(1, scroll.getTotalPages());
    const saved = parsePosition(positionQuery.data?.bookmark);
    const fraction = Number(positionQuery.data?.position_fraction ?? 0);
    const fallbackPage = Number.isFinite(fraction)
      ? Math.round(Math.min(1, Math.max(0, fraction)) * (totalPages - 1)) + 1
      : 1;
    const page = Math.max(1, Math.min(totalPages, saved?.page ?? fallbackPage));
    setTotalPages(totalPages);
    setCurrentPage(page);
    scroll.scrollToPage({ pageNumber: page, behavior: 'instant', alignY: 0 });

    const timer = window.setTimeout(() => {
      hydratingRef.current = false;
      hydratedRef.current = true;
    }, 250);
    return () => window.clearTimeout(timer);
  }, [layoutReady, positionQuery.data?.bookmark, positionQuery.data?.position_fraction,
    positionQuery.isFetched, serverAnnotations, viewerReady]);

  const listedAnnotations = useMemo(() => (serverAnnotations ?? [])
    .map((row) => ({ row, transfer: serverAnnotationTransfer(row) }))
    .filter((item): item is { row: ServerPdfAnnotation; transfer: AnnotationTransferItem } => !!item.transfer)
    .sort((left, right) => left.transfer.annotation.pageIndex - right.transfer.annotation.pageIndex),
  [serverAnnotations]);

  const showPdfAnnotation = useCallback((row: ServerPdfAnnotation) => {
    const transfer = serverAnnotationTransfer(row);
    const scroll = scrollCapabilityRef.current;
    const annotations = annotationCapabilityRef.current;
    if (!transfer || !scroll || !annotations) return;
    const pageIndex = transfer.annotation.pageIndex;
    scroll.scrollToPage({ pageNumber: pageIndex + 1, behavior: 'smooth', alignY: 0.15 });
    annotations.selectAnnotation(pageIndex, transfer.annotation.id);
    setCurrentPage(pageIndex + 1);
  }, []);

  const editPdfAnnotationNote = useCallback((row: ServerPdfAnnotation) => {
    const transfer = serverAnnotationTransfer(row);
    const annotations = annotationCapabilityRef.current;
    if (!transfer || !annotations) return;
    const note = window.prompt(t('Note'), row.note_text ?? transfer.annotation.contents ?? '');
    if (note === null) return;
    annotations.updateAnnotation(transfer.annotation.pageIndex, transfer.annotation.id, { contents: note });
  }, [t]);

  const removePdfAnnotation = useCallback((row: ServerPdfAnnotation) => {
    const transfer = serverAnnotationTransfer(row);
    const annotations = annotationCapabilityRef.current;
    if (!transfer || !annotations) return;
    annotations.deleteAnnotation(transfer.annotation.pageIndex, transfer.annotation.id);
  }, []);

  const syncBusy = pendingAnnotationSaves > 0 || saveState === 'pending' || saveState === 'saving';
  const syncFailed = annotationSaveError || saveError || saveState === 'error';

  if (sourceError) {
    return (
      <div className={styles.shell}>
        <div className={styles.bar}>
          <Link href={`/book/${id}`} className={styles.close}>
            <X size={18} aria-hidden="true" /> {t('Close')}
          </Link>
          <span className={styles.fmt}>PDF</span>
        </div>
        <div className={styles.state}>
          <EmptyState message={sourceError} />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.shell}>
      <VisuallyHidden as="h1">{t('PDF reader')}</VisuallyHidden>
      <div className={styles.bar}>
        <Link href={`/book/${id}`} className={styles.close} title={t('Close reader')} aria-label={t('Close reader')}>
          <X size={18} aria-hidden="true" focusable={false} /> {t('Close')}
        </Link>
        <span className={styles.fmt}>PDF</span>
        <span className={styles.pageStatus}>{t('Page')} {currentPage} / {totalPages}</span>
        <span className={styles.syncStatus} data-state={syncFailed ? 'error' : syncBusy ? 'busy' : 'saved'}>
          {syncFailed ? <AlertTriangle size={15} aria-hidden="true" />
            : syncBusy ? <LoaderCircle className={styles.spin} size={15} aria-hidden="true" />
              : <Check size={15} aria-hidden="true" />}
          {syncFailed ? t('Sync error') : syncBusy ? t('Saving…') : t('Synced')}
        </span>
        <div className={styles.barSpacer} />
        <button className={styles.actionButton} onClick={() => setNotesOpen((open) => !open)}
          title={t('Highlights and notes')} aria-pressed={notesOpen}>
          <StickyNote size={17} aria-hidden="true" /> <span>{t('Notes')}</span>
          {listedAnnotations.length > 0 && <span className={styles.badge}>{listedAnnotations.length}</span>}
        </button>
        <a className={styles.actionButton} href={src} target="_blank" rel="noopener"
          title={t('Open with browser PDF viewer')}>
          <ExternalLink size={17} aria-hidden="true" /> <span>{t('Browser')}</span>
        </a>
        <a className={styles.actionButton} href={downloadUrl} download target="_blank" rel="noopener"
          title={t('Download PDF')}>
          <Download size={17} aria-hidden="true" /> <span>{t('Download')}</span>
        </a>
      </div>
      <div className={styles.workspace}>
        <div className={styles.body}>
          {!sourceReady ? <SpinnerCentered size={36} /> : (
            <PDFViewer config={config} className={styles.viewer} style={{ width: '100%', height: '100%' }} onReady={onReady} />
          )}
        </div>
        {notesOpen && (
          <aside className={styles.sidePanel} aria-label={t('Highlights and notes')}>
            <div className={styles.panelHeader}>
              <div>
                <h2>{t('Highlights and notes')}</h2>
                <span>{listedAnnotations.length}</span>
              </div>
              <button className={styles.iconButton} onClick={() => setNotesOpen(false)}
                title={t('Close')} aria-label={t('Close')}><X size={18} aria-hidden="true" /></button>
            </div>
            <div className={styles.panelTools}>
              <Link href={`/book/${id}/annotations`} className={styles.panelLink}>
                {t('Open full annotations page')}
              </Link>
            </div>
            <div className={styles.annotationList}>
              {serverAnnotations === null && <SpinnerCentered size={28} />}
              {serverAnnotations !== null && listedAnnotations.length === 0 && (
                <p className={styles.emptyNotes}>{t('No PDF annotations yet. Use the Annotate tools above the document.')}</p>
              )}
              {listedAnnotations.map(({ row, transfer }) => {
                const page = transfer.annotation.pageIndex + 1;
                const note = row.note_text ?? transfer.annotation.contents;
                return (
                  <div className={styles.annotationItem} key={serverAnnotationId(row) ?? transfer.annotation.id}>
                    <button className={styles.annotationJump} onClick={() => showPdfAnnotation(row)}>
                      <strong>{t('Page')} {page}</strong>
                      <span>{annotationTypeLabel(transfer.annotation)}</span>
                      {note && <em>{note}</em>}
                    </button>
                    <div className={styles.annotationActions}>
                      <button onClick={() => editPdfAnnotationNote(row)} title={t('Edit note')}>
                        <StickyNote size={16} aria-hidden="true" /> {t('Note')}
                      </button>
                      <button className={styles.deleteButton} onClick={() => removePdfAnnotation(row)}
                        title={t('Delete')} aria-label={t('Delete')}>
                        <Trash2 size={16} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </aside>
        )}
      </div>
      {(saveError || annotationSaveError) && (
        <div className={styles.positionSaveError} role="status">
          <AlertTriangle size={16} aria-hidden="true" />
          {annotationSaveError
            ? t('PDF annotation changes could not be saved. Retry the edit.')
            : t('Reading position could not be saved. Retrying…')}
        </div>
      )}
    </div>
  );
}
