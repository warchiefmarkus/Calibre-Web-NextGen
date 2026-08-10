import { useEffect, useRef, useState } from 'react';
import { Highlighter, StickyNote, Trash2, X } from 'lucide-react';
import { useFocusTrap } from '../../../lib/a11y/useFocusTrap';
import { useT } from '../../../lib/i18n';
import styles from '../../Reader.module.css';
import { HIGHLIGHT_COLORS, type AnnotationEditorState, type HighlightColor } from './types';

export function AnnotationComposer({ state, onClose, onSave, onDelete }: {
  state: AnnotationEditorState;
  onClose: () => void;
  onSave: (color: HighlightColor, note: string) => void | Promise<void>;
  onDelete?: () => void | Promise<void>;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const [color, setColor] = useState<HighlightColor>(state.color);
  const [note, setNote] = useState(state.note);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useFocusTrap(dialogRef, { onClose });

  useEffect(() => {
    if (!state.focusNote) return;
    const frame = requestAnimationFrame(() => {
      noteRef.current?.focus();
      noteRef.current?.setSelectionRange(noteRef.current.value.length, noteRef.current.value.length);
    });
    return () => cancelAnimationFrame(frame);
  }, [state.focusNote]);

  const colorLabel = (value: HighlightColor) => ({
    yellow: t('Yellow'), green: t('Green'), blue: t('Blue'), red: t('Red'),
  })[value];
  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(color, note.trim());
    } catch {
      setError(t('Could not save annotation.'));
    } finally {
      setSaving(false);
    }
  };
  const remove = async () => {
    if (!onDelete) return;
    setSaving(true);
    setError(null);
    try { await onDelete(); } catch { setError(t('Could not delete annotation.')); }
    finally { setSaving(false); }
  };
  const standalone = state.mode === 'standalone';
  const quote = state.mode === 'create' ? state.selection.text
    : state.mode === 'edit' ? state.annotation.text : null;

  return (
    <div className={styles.annotationComposerBackdrop} onMouseDown={(e) => {
      if (e.currentTarget === e.target) onClose();
    }}>
      <div ref={dialogRef} className={styles.annotationComposer} role="dialog" aria-modal="true"
        aria-labelledby="reader-annotation-composer-title" tabIndex={-1}>
        <header className={styles.annotationComposerHeader}>
          <div>
            {standalone ? <StickyNote size={18} aria-hidden="true" /> : <Highlighter size={18} aria-hidden="true" />}
            <h2 id="reader-annotation-composer-title">
              {standalone ? t('Write a note') : state.mode === 'edit' ? t('Edit highlight') : t('Highlight')}
            </h2>
          </div>
          <button className={styles.iconButton} onClick={onClose} aria-label={t('Close')} title={t('Close')}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        {quote && <blockquote className={styles.annotationComposerQuote}>{quote}</blockquote>}
        {!standalone && <fieldset className={styles.annotationColorFieldset}>
          <legend>{t('Highlight color')}</legend>
          <div className={styles.annotationColorChoices}>
            {HIGHLIGHT_COLORS.map((value) => (
              <button key={value} type="button" className={styles.annotationColorButton}
                data-color={value} aria-label={colorLabel(value)} title={colorLabel(value)}
                aria-pressed={color === value} onClick={() => setColor(value)}>
                <span aria-hidden="true" />
              </button>
            ))}
          </div>
        </fieldset>}
        <label className={styles.annotationNoteField}>
          <span>{standalone ? t('Note') : t('Note (optional)')}</span>
          <textarea ref={noteRef} value={note} onChange={(e) => setNote(e.target.value)} rows={6}
            placeholder={standalone ? t('Write a note') : t('Add note')} />
        </label>
        {error && <p className={styles.annotationComposerError} role="alert">{error}</p>}
        <footer className={styles.annotationComposerActions}>
          {onDelete && <button className={styles.annotationDeleteAction} disabled={saving} onClick={() => void remove()}>
            <Trash2 size={17} aria-hidden="true" /> {standalone ? t('Delete note') : t('Remove highlight')}
          </button>}
          <span />
          <button onClick={onClose}>{t('Cancel')}</button>
          <button className={styles.annotationSaveAction} disabled={saving || (standalone && !note.trim())}
            onClick={() => void submit()}>{saving ? t('Saving…') : t('Save')}</button>
        </footer>
      </div>
    </div>
  );
}
