import { useCallback, useEffect, useState } from 'react';
import { Link } from 'wouter';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { apiGet, apiUrl } from '../lib/api';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import { VisuallyHidden } from '../components/VisuallyHidden';
import { useT } from '../lib/i18n';
import styles from './NativeReader.module.css';

const AUDIO = new Set(['mp3', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav', 'aac']);
const COMIC = new Set(['cbr', 'cbt', 'cb7']);

/** Native fallback for formats outside foliate-js: PDF, audio, TXT, and
 * archive comics that require server-side extraction. */
export function NativeReader({ id, format }: { id: string; format: string }) {
  const t = useT();
  const fmt = format.toLowerCase();
  const src = apiUrl(`/show/${id}/${fmt}`);
  const [text, setText] = useState<string | null>(null);
  const [textErr, setTextErr] = useState(false);

  useEffect(() => {
    if (fmt !== 'txt') return;
    let alive = true;
    fetch(src, { credentials: 'include' })
      .then((response) => response.ok ? response.text() : Promise.reject(new Error(String(response.status))))
      .then((value) => { if (alive) setText(value); })
      .catch(() => { if (alive) setTextErr(true); });
    return () => { alive = false; };
  }, [fmt, src]);
  return (
    <div className={styles.shell}>
      <VisuallyHidden as="h1">{t('{format} reader', { format: fmt.toUpperCase() })}</VisuallyHidden>
      <div className={styles.bar}>
        <Link href={`/book/${id}`} className={styles.close} title={t('Close reader')} aria-label={t('Close reader')}>
          <X size={18} aria-hidden="true" focusable={false} /> {t('Close')}
        </Link>
        <span className={styles.fmt}>{fmt.toUpperCase()}</span>
      </div>

      <div className={styles.body}>
        {fmt === 'pdf' && <iframe className={styles.pdf} src={src} title={t('PDF reader')} />}

        {AUDIO.has(fmt) && (
          <div className={styles.audioWrap}>
            <audio className={styles.audio} controls preload="metadata" src={src}>
              {t('Your browser cannot play this audio format.')}
            </audio>
          </div>
        )}

        {fmt === 'txt' && (
          textErr ? <EmptyState message={t('Could not load this text file.')} />
            : text === null ? <SpinnerCentered size={36} />
              : <pre className={styles.text}>{text}</pre>
        )}

        {COMIC.has(fmt) && <ComicViewer id={id} />}

        {!['pdf', 'txt'].includes(fmt) && !AUDIO.has(fmt) && !COMIC.has(fmt) && (
          <EmptyState message={t('No supported reader format is available.')} />
        )}
      </div>
    </div>
  );
}
/** Server-extracted fallback for CBR/CBT/CB7 archives. CBZ uses foliate-js. */
function ComicViewer({ id }: { id: string }) {
  const t = useT();
  const [pages, setPages] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    apiGet<{ pages: number }>(`/api/v1/books/${id}/comic`)
      .then((data) => { if (alive) setPages(data.pages); })
      .catch(() => { if (alive) setErr(true); });
    return () => { alive = false; };
  }, [id]);

  const go = useCallback((delta: number) => {
    setPage((current) => Math.min(Math.max(0, current + delta), (pages ?? 1) - 1));
  }, [pages]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowRight') go(1);
      if (event.key === 'ArrowLeft') go(-1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go]);

  if (err) return <EmptyState message={t('Could not read this comic archive.')} />;
  if (pages === null) return <SpinnerCentered size={36} />;
  if (pages === 0) return <EmptyState message={t('No pages found in this comic.')} />;
  return (
    <div className={styles.comic}>
      <button className={styles.comicNav} onClick={() => go(-1)} disabled={page === 0}
        aria-label={t('Previous page')}><ChevronLeft size={28} /></button>
      <img className={styles.comicPage} src={apiUrl(`/api/v1/books/${id}/comic/${page}`)}
        alt={t('Page {number}', { number: page + 1 })} />
      <button className={styles.comicNav} onClick={() => go(1)} disabled={page >= pages - 1}
        aria-label={t('Next page')}><ChevronRight size={28} /></button>
      <div className={styles.comicPager}>{page + 1} / {pages}</div>
    </div>
  );
}
