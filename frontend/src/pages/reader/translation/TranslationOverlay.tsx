import { createElement, type RefObject } from 'react';
import type { ReaderSettings } from '../../../lib/queries';
import { READER_THEME } from '../settings/readerStyle';
import { translatedInlineContent, type StyledTranslationBlock, type TranslationContentSegment, type TranslationPageLayout } from './translationPage';
import styles from '../../Reader.module.css';

export function TranslationOverlay(props: {
  settings: ReaderSettings;
  layout: TranslationPageLayout;
  segments: TranslationContentSegment[];
  blocks: StyledTranslationBlock[];
  pageIndex: number;
  overlayRef: RefObject<HTMLDivElement>;
  pagerContentRef: RefObject<HTMLDivElement>;
}) {
  const blocks = new Map(props.blocks.map((block) => [block.id, block]));
  return <div ref={props.overlayRef} className={styles.translationOverlay}
    data-reader-translation-overlay data-flow={props.settings.flow} aria-live="polite"
    style={{ background: READER_THEME[props.settings.theme].background, color: READER_THEME[props.settings.theme].text }}>
    <div className={styles.translationPageShell} style={{
      width: `${props.layout.pageWidth}px`,
      height: props.settings.flow === 'scrolled' ? 'auto' : `${props.layout.pageHeight}px`,
      minHeight: props.settings.flow === 'scrolled' ? '100%' : undefined,
    }}>
      <div className={styles.translationPagerViewport}>
        <div ref={props.pagerContentRef} className={styles.translationPagerContent} style={{
          left: props.settings.flow === 'scrolled' ? 'auto' : `${props.layout.paddingInlineStart}px`,
          top: props.settings.flow === 'scrolled' ? 'auto' : `${props.layout.paddingBlockStart}px`,
          width: `${props.layout.contentWidth}px`,
          height: props.settings.flow === 'scrolled' ? 'auto' : `${props.layout.contentHeight}px`,
          minHeight: props.settings.flow === 'scrolled' ? `${props.layout.contentHeight}px` : undefined,
          paddingBlock: props.settings.flow === 'scrolled'
            ? `${props.layout.paddingBlockStart}px ${props.layout.paddingBlockEnd}px` : undefined,
          columnWidth: props.settings.flow === 'scrolled' ? 'auto' : `${props.layout.contentWidth}px`,
          columnGap: props.settings.flow === 'scrolled' ? 'normal' : `${props.layout.columnGap}px`,
          transform: props.settings.flow === 'scrolled' ? 'none'
            : `translate3d(${-props.pageIndex * (props.layout.contentWidth + props.layout.columnGap)}px, 0, 0)`,
          ...props.layout.defaultStyle,
        }}>
          {props.segments.map((segment) => {
            if (segment.kind === 'image') return <img key={segment.id} className={styles.translationImage}
              data-source-image-id={segment.id} src={segment.src} alt={segment.alt} title={segment.title}
              style={segment.style} draggable={false} />;
            const block = blocks.get(segment.id);
            if (!block) return null;
            return createElement(block.tag, {
              key: block.id, className: styles.translationBlock,
              'data-source-block-id': block.id, style: block.style,
            }, translatedInlineContent(block));
          })}
        </div>
      </div>
    </div>
  </div>;
}
