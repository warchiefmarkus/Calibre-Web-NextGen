import { useCallback, useEffect, useRef, useState } from 'react';
import { apiPost } from './api';

const LS_DEVICE = 'cwng.reader.device';
const DEFAULT_DELAY_MS = 800;
const RETRY_DELAY_MS = 3000;

export interface PendingReadingPosition {
  bookmark: string;
  positionFraction: number;
}

export function readerDevice(): string {
  let value = localStorage.getItem(LS_DEVICE);
  if (!value) {
    value = `cwng-web-${crypto.randomUUID()}`;
    localStorage.setItem(LS_DEVICE, value);
  }
  return value;
}

export function clampReadingFraction(value: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}

export function fb2ScrollBookmark(fraction: number): string {
  return `fb2-scroll:${clampReadingFraction(fraction).toFixed(8)}`;
}

export function parseFb2ScrollBookmark(bookmark: string | null | undefined): number | null {
  const match = /^fb2-scroll:(0(?:\.\d+)?|1(?:\.0+)?)$/i.exec(bookmark || '');
  if (!match) return null;
  return clampReadingFraction(Number(match[1]));
}

/** Debounced writes plus lifecycle flushes. A pending position is flushed when
 * the tab is hidden, the page is left, or the reader component unmounts. */
export function useReadingPositionSaver(
  bookId: string | number,
  format: string,
  delayMs = DEFAULT_DELAY_MS,
) {
  const pendingRef = useRef<PendingReadingPosition | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const [saveError, setSaveError] = useState(false);

  const flush = useCallback(async (keepalive = false): Promise<void> => {
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    try {
      await apiPost(`/api/v1/books/${bookId}/bookmark`, {
        format,
        bookmark: pending.bookmark,
        position_fraction: clampReadingFraction(pending.positionFraction),
        device: readerDevice(),
      }, { keepalive });
      if (mountedRef.current) setSaveError(false);
    } catch {
      // Preserve the newest unsaved value. A newer scroll/page-turn always wins.
      if (pendingRef.current === null) pendingRef.current = pending;
      if (mountedRef.current) {
        setSaveError(true);
        if (timerRef.current === null) {
          timerRef.current = setTimeout(() => {
            timerRef.current = null;
            void flush(false);
          }, RETRY_DELAY_MS);
        }
      }
    }
  }, [bookId, format]);

  const schedule = useCallback((bookmark: string, positionFraction: number) => {
    pendingRef.current = {
      bookmark,
      positionFraction: clampReadingFraction(positionFraction),
    };
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void flush(false);
    }, delayMs);
  }, [delayMs, flush]);

  useEffect(() => {
    mountedRef.current = true;
    const onPageHide = () => { void flush(true); };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') void flush(true);
    };
    window.addEventListener('pagehide', onPageHide);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      void flush(true);
      mountedRef.current = false;
    };
  }, [flush]);

  return { schedule, flush, saveError };
}
