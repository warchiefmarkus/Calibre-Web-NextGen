import type { ReadingProgressSummary } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './CoverProgressBadge.module.css';

interface CoverProgressBadgeProps {
  progress?: ReadingProgressSummary | null;
}

export function formatReadingProgress(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

export function CoverProgressBadge({ progress }: CoverProgressBadgeProps) {
  const t = useT();
  if (!progress || !Number.isFinite(progress.percentage)) return null;
  const percent = formatReadingProgress(progress.percentage);
  const label = `${t('Reading progress')}: ${percent}%`;

  return (
    <span className={styles.badge} data-cover-progress
      aria-label={label} title={label}>
      {percent}%
    </span>
  );
}
