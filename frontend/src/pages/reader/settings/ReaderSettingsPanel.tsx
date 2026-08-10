import { AlignJustify, Columns2 } from 'lucide-react';
import type { ReaderSettings } from '../../../lib/queries';
import { useT } from '../../../lib/i18n';
import styles from '../../Reader.module.css';

const FONT_MIN = 75;
const FONT_MAX = 200;

export function ReaderSettingsPanel({ settings, update }: {
  settings: ReaderSettings;
  update: (patch: Partial<ReaderSettings>) => void;
}) {
  const t = useT();
  return (
    <div className={styles.settingsPanel}>
      <label>{t('Reading mode')}
        <select value={settings.flow} onChange={(event) => update({ flow: event.target.value as ReaderSettings['flow'] })}>
          <option value="paginated">{t('Pages')}</option>
          <option value="scrolled">{t('Continuous scroll')}</option>
        </select>
      </label>
      <label>{t('Columns')}
        <select value={settings.spread} onChange={(event) => update({ spread: event.target.value as ReaderSettings['spread'] })}
          disabled={settings.flow === 'scrolled'}>
          <option value="nonespread">{t('One column')}</option>
          <option value="spread">{t('Two columns')}</option>
        </select>
      </label>
      <label>{t('Theme')}
        <select value={settings.theme} onChange={(event) => update({ theme: event.target.value as ReaderSettings['theme'] })}>
          <option value="lightTheme">{t('Light')}</option>
          <option value="sepiaTheme">{t('Sepia')}</option>
          <option value="darkTheme">{t('Dark')}</option>
          <option value="blackTheme">{t('Black')}</option>
        </select>
      </label>
      <label>{t('Font')}
        <select value={settings.font} onChange={(event) => update({ font: event.target.value as ReaderSettings['font'] })}>
          <option value="default">{t('Publisher / serif')}</option>
          <option value="Arial">Arial</option>
          <option value="Yahei">Microsoft YaHei</option>
          <option value="SimSun">SimSun</option>
          <option value="KaiTi">KaiTi</option>
        </select>
      </label>
      <label>{t('Font size')} <output>{settings.fontSize}%</output>
        <input type="range" min={FONT_MIN} max={FONT_MAX} value={settings.fontSize}
          onChange={(event) => update({ fontSize: Number(event.target.value) })} />
      </label>
      <label>{t('Line height')} <output>{(settings.lineHeight / 100).toFixed(1)}</output>
        <input type="range" min={100} max={220} step={5} value={settings.lineHeight}
          onChange={(event) => update({ lineHeight: Number(event.target.value) })} />
      </label>
      <label>{t('Page margins')} <output>{settings.margin}px</output>
        <input type="range" min={0} max={80} step={4} value={settings.margin}
          onChange={(event) => update({ margin: Number(event.target.value) })} />
      </label>
      <label>{t('Text width')} <output>{settings.maxInlineSize}px</output>
        <input type="range" min={420} max={1200} step={20} value={settings.maxInlineSize}
          onChange={(event) => update({ maxInlineSize: Number(event.target.value) })} />
      </label>
      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.justifyText}
          onChange={(event) => update({ justifyText: event.target.checked })} />
        {t('Justify text')}
      </label>
      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.animated}
          onChange={(event) => update({ animated: event.target.checked })} />
        {t('Animated page turns')}
      </label>
      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.tapToTurn}
          onChange={(event) => update({ tapToTurn: event.target.checked })} />
        {t('Turn pages by clicking the left or right side')}
      </label>
      <div className={styles.settingsHint}>
        <AlignJustify size={18} aria-hidden="true" />
        <span>{t('Reader settings are saved to your account and follow you across devices.')}</span>
      </div>
      <div className={styles.settingsHint}>
        <Columns2 size={18} aria-hidden="true" />
        <span>{t('Page count changes with font, margins, and window size; progress remains stable.')}</span>
      </div>
    </div>
  );
}
