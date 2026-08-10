import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faOpenai } from '@fortawesome/free-brands-svg-icons';
import { Highlighter, Languages, StickyNote, X } from 'lucide-react';
import { useT } from '../../lib/i18n';
import styles from '../Reader.module.css';

export function SelectionActions(props: {
  text: string;
  openHighlight: (withNote: boolean) => void;
  translate: () => void;
  translationLoading: boolean;
  openChatGpt: () => void;
  dismiss: () => void;
  error: string | null;
}) {
  const t = useT();
  return <div className={styles.selectionBar} role="toolbar" aria-label={t('Selected text actions')}>
    <span>{props.text.slice(0, 120)}</span>
    <button onClick={() => props.openHighlight(false)}><Highlighter size={17} aria-hidden="true" /> {t('Highlight')}</button>
    <button onClick={() => props.openHighlight(true)}><StickyNote size={17} aria-hidden="true" /> {t('Add note')}</button>
    <button onClick={props.translate} disabled={props.translationLoading}><Languages size={17} aria-hidden="true" /> {t('Translation')}</button>
    <button onClick={props.openChatGpt} title={t('Open selected text in ChatGPT')} aria-label={t('Open selected text in ChatGPT')}>
      <FontAwesomeIcon icon={faOpenai} className={styles.selectionChatGptIcon} aria-hidden="true" /> ChatGPT
    </button>
    <button onClick={props.dismiss}><X size={17} aria-hidden="true" /> {t('Cancel')}</button>
    {props.error && <span className={styles.selectionError} role="alert">{props.error}</span>}
  </div>;
}
