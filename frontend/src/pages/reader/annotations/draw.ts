// @ts-expect-error foliate-js intentionally ships browser JavaScript without declarations.
import { Overlayer } from '../../../vendor/foliate-js/overlayer.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

const HIGHLIGHT_FILL: Record<string, string> = {
  yellow: '#e6c34a',
  red: '#d9534f',
  green: '#5cb85c',
  blue: '#5b9bd5',
  pink: '#e8afcf',
  grey: '#a0a0a0',
};

// Deliberately not a palette colour: an unresolved device colour must stay
// visibly unknown instead of being silently re-labelled as yellow.
export const UNKNOWN_FILL = '#d0cbc2';

export function annotationFill(value?: string | null): string {
  const normalized = String(value ?? '').trim().toLowerCase();
  return HIGHLIGHT_FILL[normalized] ?? UNKNOWN_FILL;
}

/** Foliate overlay painter with a non-colour cue when a highlight has a note. */
export function drawFoliateHighlight(rects: DOMRectList | DOMRect[], options: {
  color?: string;
  hasNote?: boolean;
} = {}) {
  const color = annotationFill(options.color);
  const fill = Overlayer.highlight(rects, { color });
  if (!options.hasNote) return fill;
  const group = document.createElementNS(SVG_NS, 'g');
  const outline = Overlayer.outline(rects, { color, width: 1.5, radius: 2 });
  outline.setAttribute('stroke-dasharray', '3 2');
  outline.setAttribute('stroke-opacity', '.95');
  group.append(fill, outline);
  return group;
}
