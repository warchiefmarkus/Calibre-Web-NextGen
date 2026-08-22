import { Link } from 'wouter';
import {
  Bookmark, BookOpen, Languages, List, Maximize, Minimize, Search, Settings,
  Square, StickyNote, Volume2, X,
} from 'lucide-react';
import type { ReaderSettings } from '../../lib/queries';
import { useT } from '../../lib/i18n';
import type { ReaderPanel } from './ReaderSidePanel';
import styles from '../Reader.module.css';

export function ReaderToolbar(props: {
  bookId: string;
  title: string;
  format: string;
  panel: ReaderPanel;
  setPanel: (panel: ReaderPanel) => void;
  canBookmark: boolean;
  addBookmark: () => void;
  speaking: boolean;
  toggleSpeech: () => void;
  settings: ReaderSettings | null;
  updateSettings: (patch: Partial<ReaderSettings>) => void;
  translationSkipped: boolean;
  translationActivity: boolean;
  translationLoading: boolean;
  translationPreloading: boolean;
  fullscreenSupported: boolean;
  isFullscreen: boolean;
  toggleFullscreen: () => void;
}) {
  const t = useT();
  const togglePanel = (value: Exclude<ReaderPanel, null>) => props.setPanel(props.panel === value ? null : value);
  return <header className={styles.topBar}>
    <Link href={`/book/${props.bookId}`} className={styles.iconButton} title={t('Close reader')} aria-label={t('Close reader')}>
      <X size={20} aria-hidden="true" />
    </Link>
    <div className={styles.bookIdentity}>
      <strong>{props.title}</strong><span>{props.format.toUpperCase()}</span>
    </div>
    <nav className={styles.toolbar} aria-label={t('Reader tools')}>
      <button className={styles.iconButton} onClick={() => togglePanel('toc')}
        title={t('Table of contents')} aria-pressed={props.panel === 'toc'}><List size={19} aria-hidden="true" /></button>
      <button className={styles.iconButton} onClick={() => togglePanel('search')}
        title={t('Search in book')} aria-pressed={props.panel === 'search'}><Search size={19} aria-hidden="true" /></button>
      <button className={styles.iconButton} onClick={props.addBookmark}
        title={t('Add bookmark')} aria-label={t('Add bookmark')} disabled={!props.canBookmark}>
        <Bookmark size={19} aria-hidden="true" />
      </button>
      <button className={styles.iconButton} onClick={() => togglePanel('bookmarks')}
        title={t('Bookmarks')} aria-pressed={props.panel === 'bookmarks'}><BookOpen size={19} aria-hidden="true" /></button>
      <button className={styles.iconButton} onClick={() => togglePanel('notes')}
        title={t('Highlights and notes')} aria-pressed={props.panel === 'notes'}><StickyNote size={19} aria-hidden="true" /></button>
      <button className={styles.iconButton} onClick={props.toggleSpeech}
        title={props.speaking ? t('Stop reading aloud') : t('Read aloud')} aria-pressed={props.speaking}>
        {props.speaking ? <Square size={18} aria-hidden="true" /> : <Volume2 size={19} aria-hidden="true" />}
      </button>
      {props.settings?.translationProfileId && <div className={styles.translationToggle} role="group" aria-label={t('Page language view')}>
        <button type="button" className={props.settings.translationView === 'original' || props.translationSkipped ? styles.translationToggleActive : ''}
          onClick={() => props.updateSettings({ translationView: 'original' })}
          aria-pressed={props.settings.translationView === 'original' || props.translationSkipped}
          aria-label={t('Original')} title={`${t('Original')} (T)`}><BookOpen size={16} aria-hidden="true" /></button>
        <button type="button" className={props.settings.translationView === 'translated' && !props.translationSkipped ? styles.translationToggleActive : ''}
          onClick={() => props.updateSettings({ translationEnabled: true, translationView: 'translated' })}
          aria-pressed={props.settings.translationView === 'translated' && !props.translationSkipped}
          aria-busy={props.translationActivity}
          data-translation-activity={props.translationLoading ? 'translation' : props.translationPreloading ? 'preload' : 'idle'}
          aria-label={t('Translation')} title={`${t('Translation')} (T)`}>
          <span className={styles.translationToggleIcon}>
            <Languages size={16} aria-hidden="true" />
            {props.translationActivity && <span className={styles.translationActivityRing} aria-hidden="true" />}
          </span>
        </button>
      </div>}
      <button className={styles.iconButton} onClick={() => togglePanel('translation')}
        title={t('Page translation')} aria-pressed={props.panel === 'translation'}><Languages size={19} aria-hidden="true" /></button>
      <button className={styles.iconButton} onClick={() => togglePanel('settings')}
        title={t('Reader settings')} aria-pressed={props.panel === 'settings'}><Settings size={19} aria-hidden="true" /></button>
      {props.fullscreenSupported && <button className={styles.iconButton} onClick={props.toggleFullscreen}
        title={props.isFullscreen ? t('Exit full screen') : t('Full screen')}
        aria-label={props.isFullscreen ? t('Exit full screen') : t('Full screen')} aria-pressed={props.isFullscreen}>
        {props.isFullscreen ? <Minimize size={19} aria-hidden="true" /> : <Maximize size={19} aria-hidden="true" />}
      </button>}
    </nav>
  </header>;
}
