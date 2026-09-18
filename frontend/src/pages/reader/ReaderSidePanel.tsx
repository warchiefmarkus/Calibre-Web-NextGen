import { useRef } from 'react';
import { Trash2, X } from 'lucide-react';
import type { ReaderBookmark, ReaderSettings, ReadingSource } from '../../lib/queries';
import { useFocusTrap } from '../../lib/a11y/useFocusTrap';
import { useT } from '../../lib/i18n';
import { formatReadingProgress } from '../../lib/readerProgress';
import type { FoliateAnnotation, SearchResult, TocItem } from './FoliateEngine';
import { AnnotationPanel } from './annotations/AnnotationPanel';
import { ReaderSettingsPanel } from './settings/ReaderSettingsPanel';
import { ReaderTranslationSettings } from './translation/ReaderTranslationSettings';
import styles from '../Reader.module.css';

export type ReaderPanel = 'toc' | 'search' | 'bookmarks' | 'notes' | 'places' | 'settings' | 'translation' | null;

function excerptText(excerpt: SearchResult['excerpt']): string {
  return `${excerpt.pre}${excerpt.match}${excerpt.post}`;
}


function readingSourceTime(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium', timeStyle: 'short',
  }).format(date);
}

export function ReaderSidePanel(props: {
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
  readingSources: ReadingSource[];
  readingSourcesLoading: boolean;
  readingSourcesError: boolean;
  readingSourcesStorytellerUnavailable: boolean;
  openingReadingSource: string | null;
  previewReadingSource: ReadingSource | null;
  openReadingSource: (source: ReadingSource) => void;
  annotations: FoliateAnnotation[];
  createStandaloneNote: () => void;
  showAnnotation: (annotation: FoliateAnnotation) => void;
  editAnnotation: (annotation: FoliateAnnotation) => void;
  removeAnnotation: (annotation: FoliateAnnotation) => void;
  settings: ReaderSettings | null;
  updateSettings: (patch: Partial<ReaderSettings>) => void;
}) {
  const t = useT();
  const panelRef = useRef<HTMLElement>(null);
  useFocusTrap(panelRef, { onClose: props.onClose });
  const title = {
    toc: t('Table of contents'), search: t('Search in book'),
    bookmarks: t('Bookmarks'), notes: t('Highlights and notes'),
    places: t('Reading places'), settings: t('Reader settings'), translation: t('Translation'),
  }[props.panel];

  return (
    <aside ref={panelRef} className={styles.sidePanel} role="dialog" aria-modal="true"
      aria-label={title} tabIndex={-1}>
      <header className={styles.panelHeader}>
        <h2>{title}</h2>
        <button className={styles.iconButton} onClick={props.onClose} title={t('Close')} aria-label={t('Close')}>
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      {props.panel === 'toc' && <div className={styles.panelList}>
        {props.toc.length === 0 && <p className={styles.muted}>{t('No contents found.')}</p>}
        {props.toc.map((item, index) => <button key={`${item.href}-${index}`} className={styles.listButton}
          style={{ paddingInlineStart: `${12 + item.depth * 16}px` }} disabled={!item.href}
          onClick={() => item.href && props.onNavigate(item.href)}>{item.label || t('Untitled')}</button>)}
      </div>}
      {props.panel === 'search' && <div className={styles.panelBody}>
        <form className={styles.searchForm} onSubmit={(event) => { event.preventDefault(); props.runSearch(); }}>
          <input value={props.searchText} onChange={(event) => props.setSearchText(event.target.value)}
            placeholder={t('Search in book')} aria-label={t('Search in book')} />
          <button type="submit" disabled={!props.searchText.trim() || props.searching}>{t('Search')}</button>
        </form>
        {props.searching && <progress max={1} value={props.searchProgress} />}
        <div className={styles.panelList}>{props.searchResults.map((result, index) => (
          <button key={`${result.cfi}-${index}`} className={styles.searchResult} onClick={() => props.onNavigate(result.cfi)}>
            <strong>{result.label || t('Book')}</strong><span>{excerptText(result.excerpt)}</span>
          </button>
        ))}</div>
      </div>}
      {props.panel === 'bookmarks' && <div className={styles.panelList}>
        {props.bookmarks.length === 0 && <p className={styles.muted}>{t('No bookmarks yet.')}</p>}
        {props.bookmarks.map((bookmark) => <div className={styles.savedItem} key={bookmark.bookmark_id}>
          <button onClick={() => props.openBookmark(bookmark)}>
            <strong>{bookmark.chapter || t('Bookmark')}</strong>
            <span>{formatReadingProgress(bookmark.progression * 100)}%{bookmark.label ? ` · ${bookmark.label}` : ''}</span>
          </button>
          <button className={styles.deleteButton} onClick={() => props.deleteBookmark(bookmark.bookmark_id)}
            aria-label={t('Delete')} title={t('Delete')}><Trash2 size={16} aria-hidden="true" /></button>
        </div>)}
      </div>}
      {props.panel === 'places' && <div className={styles.placesPanelBody}>
        <p className={styles.placesIntro}>
          {t('Preview a saved place in this reader. The device’s saved position will not change.')}
        </p>
        {props.readingSourcesLoading ? (
          <p className={styles.muted} role="status">{t('Loading reading places…')}</p>
        ) : props.readingSourcesError ? (
          <p className={styles.placesError} role="alert">{t('Could not load reading places.')}</p>
        ) : props.readingSources.length ? (
          <ul className={styles.placesList} role="list">
            {props.readingSources.map((source) => {
              const sourceTime = readingSourceTime(source.observed_at);
              const selected = props.previewReadingSource?.id === source.id;
              return <li key={source.id} className={selected ? styles.placeSelected : styles.place}>
                <div className={styles.placeHeading}>
                  <strong>{source.observation === 'resolved' ? t('Other saved position') : source.label}</strong>
                  <span>{source.progress_percent == null
                    ? t('Place saved')
                    : t('{percent}% read', { percent: formatReadingProgress(source.progress_percent) })}</span>
                </div>
                <div className={styles.placeTags}>
                  {source.observation !== 'resolved' && <span>{t('Last reported')}</span>}
                  <span>{source.resume.exact ? t('Exact place') : t('Approximate')}</span>
                  {source.provenance === 'unknown' && <span>{t('Source not recorded')}</span>}
                  {source.edition?.match === 'different' && <span>{t('Different edition')}</span>}
                </div>
                {sourceTime && <small>{source.observation === 'resolved'
                  ? t('Saved {time}', { time: sourceTime })
                  : t('Last reported {time}', { time: sourceTime })}</small>}
                <button type="button" className={styles.placeOpen}
                  disabled={props.openingReadingSource !== null}
                  onClick={() => props.openReadingSource(source)}>
                  {props.openingReadingSource === source.id ? t('Opening…') : t('Preview this place')}
                </button>
              </li>;
            })}
          </ul>
        ) : (
          <p className={styles.muted}>{t('No saved reading places yet.')}</p>
        )}
        {props.readingSourcesStorytellerUnavailable && (
          <p className={styles.placesError} role="status">
            {t('Storyteller is configured but could not be reached.')}
          </p>
        )}
        <p className={styles.placesFootnote}>
          {t('Device positions are read-only here. Continue reading to save a new Browser position.')}
        </p>
      </div>}
      {props.panel === 'notes' && <AnnotationPanel annotations={props.annotations}
        onCreateStandalone={props.createStandaloneNote} onOpen={props.showAnnotation}
        onEdit={props.editAnnotation} onRemove={props.removeAnnotation} />}
      {props.panel === 'settings' && props.settings && <ReaderSettingsPanel settings={props.settings} update={props.updateSettings} />}
      {props.panel === 'translation' && props.settings && <div className={styles.translationPanel}>
        <ReaderTranslationSettings settings={props.settings} update={props.updateSettings} />
      </div>}
    </aside>
  );
}
