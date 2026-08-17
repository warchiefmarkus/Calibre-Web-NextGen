import type { ReadingProgressSummary } from '../lib/api';
import { useT } from '../lib/i18n';
import { formatReadingProgress } from '../lib/readerProgress';
import styles from './CoverProgressBadge.module.css';

interface CoverProgressBadgeProps {
  progress?: ReadingProgressSummary | null;
  side?: 'left' | 'right';
}

export function CoverProgressBadge({ progress, side = 'left' }: CoverProgressBadgeProps) {
  const t = useT();
  if (!progress || !Number.isFinite(progress.percentage)) return null;
  const percent = formatReadingProgress(progress.percentage);
  const label = `${t('Reading progress')}: ${percent}%`;

  return (
    <span className={`${styles.badge} ${side === 'right' ? styles.right : ''}`} data-cover-progress
      aria-label={label} title={label}>
      <span className={styles.label}>{percent}%</span>
    </span>
  );
}
