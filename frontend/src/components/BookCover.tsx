import { resourceUrl, type ExternalRatingSummary } from '../lib/api';
import { CoverRatingBadge } from './CoverRatingBadge';
import styles from './BookCover.module.css';

interface BookCoverProps {
  coverUrl?: string | null;
  title: string;
  authors?: string[];
  externalRating?: ExternalRatingSummary | null;
}

export function BookCover({ coverUrl, title, authors, externalRating }: BookCoverProps) {
  return (
    <div className={styles.wrap}>
      {coverUrl ? (
        <img
          src={resourceUrl(coverUrl)}
          alt={title}
          loading="lazy"
          className={styles.img}
        />
      ) : (
        <div className={styles.fallback} aria-label={title}>
          <span className={styles.fallbackTitle}>{title}</span>
          {authors?.[0] && <span className={styles.fallbackAuthor}>{authors[0]}</span>}
          <span className={styles.fallbackMark} aria-hidden="true">NextGen</span>
        </div>
      )}
      <CoverRatingBadge rating={externalRating} />
    </div>
  );
}
