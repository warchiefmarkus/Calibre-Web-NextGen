import { Star } from 'lucide-react';
import type { ExternalRatingSummary } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './CoverRatingBadge.module.css';

const SOURCE_LABELS: Record<string, string> = {
  goodreads: 'Goodreads',
  hardcover: 'Hardcover',
  google_books: 'Google Books',
  open_library: 'Open Library',
};

interface CoverRatingBadgeProps {
  rating?: ExternalRatingSummary | null;
}

export function CoverRatingBadge({ rating }: CoverRatingBadgeProps) {
  const t = useT();
  if (!rating || !Number.isFinite(rating.rating)) return null;

  const source = SOURCE_LABELS[rating.source] ?? rating.source;
  const score = rating.rating.toFixed(1);
  const voteText = rating.ratings_count != null
    ? t('Ratings: {count}', { count: new Intl.NumberFormat().format(rating.ratings_count) })
    : null;
  const label = voteText ? `${source}: ${score} / 5 · ${voteText}` : `${source}: ${score} / 5`;

  return (
    <span className={styles.badge} role="img" aria-label={label} title={label}>
      <Star className={styles.star} size={13} fill="currentColor" aria-hidden="true" />
      <span>{score}</span>
    </span>
  );
}
