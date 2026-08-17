import { Highlighter, Pencil, StickyNote, Trash2 } from 'lucide-react';
import type { FoliateAnnotation } from '../FoliateEngine';
import { useT } from '../../../lib/i18n';
import styles from '../../Reader.module.css';
import { annotationColor } from './types';

export function AnnotationPanel({ annotations, onCreateStandalone, onOpen, onEdit, onRemove }: {
  annotations: FoliateAnnotation[];
  onCreateStandalone: () => void;
  onOpen: (annotation: FoliateAnnotation) => void;
  onEdit: (annotation: FoliateAnnotation) => void;
  onRemove: (annotation: FoliateAnnotation) => void;
}) {
  const t = useT();
  return (
    <div className={styles.panelList}>
      <button className={styles.annotationNewNote} onClick={onCreateStandalone}>
        <StickyNote size={16} aria-hidden="true" />
        {t('Write a note')}
      </button>
      {annotations.length === 0 && <p className={styles.muted}>{t('No highlights or notes yet.')}</p>}
      {annotations.map((annotation) => (
        <div className={styles.annotationItem} key={annotation.id ?? annotation.value}>
          {annotation.unanchored ? (
            <div className={styles.annotationStandaloneBody}
              title={t('A note about the book, not tied to a passage')}>
              <StickyNote size={15} aria-hidden="true" />
              <span className={styles.annotationNote}>{annotation.note}</span>
              {annotation.sourceLabel && <span className={styles.annotationSource}>{annotation.sourceLabel}</span>}
            </div>
          ) : (
            <button onClick={() => onOpen(annotation)}>
              <span className={styles.annotationText}>
                <span className={styles.annotationColorDot} data-color={annotationColor(annotation.color)} />
                <Highlighter size={14} aria-hidden="true" />
                {annotation.text || t('Highlight')}
              </span>
              {annotation.note && <span className={styles.annotationNote}>{annotation.note}</span>}
              {annotation.sourceLabel && <span className={styles.annotationSource}>{annotation.sourceLabel}</span>}
            </button>
          )}
          <div className={styles.itemActions}>
            <button onClick={() => onEdit(annotation)} aria-label={t('Edit note')} title={t('Edit note')}>
              <Pencil size={16} aria-hidden="true" />
            </button>
            <button className={styles.deleteButton} onClick={() => onRemove(annotation)}
              aria-label={t('Delete')} title={t('Delete')}>
              <Trash2 size={16} aria-hidden="true" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
