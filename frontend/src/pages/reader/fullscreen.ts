import { useCallback, useEffect, useState, type RefObject } from 'react';

type WebkitDocument = Document & {
  webkitFullscreenEnabled?: boolean;
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => void;
};

type WebkitElement = HTMLElement & {
  webkitRequestFullscreen?: () => void | Promise<void>;
};

export function fullscreenSupported(): boolean {
  if (typeof document === 'undefined') return false;
  const doc = document as WebkitDocument;
  return !!(doc.fullscreenEnabled || doc.webkitFullscreenEnabled);
}

export function fullscreenElement(): Element | null {
  if (typeof document === 'undefined') return null;
  const doc = document as WebkitDocument;
  return doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null;
}

export function useReaderFullscreen(targetRef: RefObject<HTMLElement>) {
  const [supported] = useState(fullscreenSupported);
  const [isFullscreen, setIsFullscreen] = useState(() => !!fullscreenElement());

  const toggleFullscreen = useCallback(() => {
    const doc = document as WebkitDocument;
    if (fullscreenElement()) {
      try {
        const result = doc.exitFullscreen ? doc.exitFullscreen() : doc.webkitExitFullscreen?.();
        if (result && typeof (result as Promise<void>).catch === 'function') {
          void (result as Promise<void>).catch(() => undefined);
        }
      } catch { /* browser rejected fullscreen exit */ }
      return;
    }
    const element = targetRef.current as WebkitElement | null;
    if (!element) return;
    try {
      const request = element.requestFullscreen?.bind(element)
        ?? element.webkitRequestFullscreen?.bind(element);
      const result = request?.();
      if (result && typeof (result as Promise<void>).catch === 'function') {
        void (result as Promise<void>).catch(() => undefined);
      }
    } catch { /* unsupported/refused; keep reader usable */ }
  }, [targetRef]);

  useEffect(() => {
    if (!supported) return;
    const sync = () => setIsFullscreen(!!fullscreenElement());
    document.addEventListener('fullscreenchange', sync);
    document.addEventListener('webkitfullscreenchange', sync);
    sync();
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      document.removeEventListener('webkitfullscreenchange', sync);
    };
  }, [supported]);

  return { fullscreenSupported: supported, isFullscreen, toggleFullscreen };
}
