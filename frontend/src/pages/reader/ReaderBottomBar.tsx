import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useT } from '../../lib/i18n';
import type { FoliateLocation } from './FoliateEngine';
import styles from '../Reader.module.css';

export function ReaderBottomBar(props: {
  location: FoliateLocation;
  percent: string;
  progress: number;
  sectionFractions: number[];
  previous: () => void;
  next: () => void;
  changeProgress: (fraction: number) => void;
}) {
  const t = useT();
  return <footer className={styles.bottomBar}>
    <button className={styles.pageButton} onClick={props.previous} title={t('Previous page')} aria-label={t('Previous page')}>
      <ChevronLeft size={22} aria-hidden="true" />
    </button>
    <div className={styles.progressArea}>
      <div className={styles.progressMeta}>
        <span>{props.location.tocItem?.label || t('Book')}</span>
        <span>{props.location.pageItem?.label ? `${t('Page')} ${props.location.pageItem.label} · ` : ''}
          {props.location.location?.current && props.location.location?.total
            ? `${t('Location')} ${props.location.location.current}/${props.location.location.total} · ` : ''}{props.percent}%</span>
      </div>
      <input className={styles.progressSlider} type="range" min={0} max={1000}
        value={Math.round(props.progress * 1000)} list="reader-section-marks" aria-label={t('Reading progress')}
        onChange={(event) => props.changeProgress(Number(event.target.value) / 1000)} />
      <datalist id="reader-section-marks">
        {props.sectionFractions.map((fraction) => <option key={fraction} value={Math.round(fraction * 1000)} />)}
      </datalist>
    </div>
    <button className={styles.pageButton} onClick={props.next} title={t('Next page')} aria-label={t('Next page')}>
      <ChevronRight size={22} aria-hidden="true" />
    </button>
  </footer>;
}
