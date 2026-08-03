import { Star } from 'lucide-react';
import type { ExternalRatingSummary } from '../lib/api';
import { useT } from '../lib/i18n';
import { EXTERNAL_RATING_SOURCE_LABELS, formatExternalRatingScore } from '../lib/externalRating';
import styles from './CoverRatingBadge.module.css';

interface CoverRatingBadgeProps {
  rating?: ExternalRatingSummary | null;
}

export function CoverRatingBadge({ rating }: CoverRatingBadgeProps) {
  const t = useT();
  if (!rating || !Number.isFinite(rating.rating)) return null;

  const source = EXTERNAL_RATING_SOURCE_LABELS[rating.source] ?? rating.source;
  const score = formatExternalRatingScore(rating.rating);
  const voteText = rating.ratings_count != null
    ? t('Ratings: {count}', { count: new Intl.NumberFormat().format(rating.ratings_count) })
    : null;
  const label = voteText ? `${source}: ${score} / 5 · ${voteText}` : `${source}: ${score} / 5`;

  return (
    <span className={styles.badge} data-cover-rating role="img" aria-label={label} title={label}>
      <Star className={styles.star} size={13} fill="currentColor" aria-hidden="true" />
      <span>{score}</span>
    </span>
  );
}
