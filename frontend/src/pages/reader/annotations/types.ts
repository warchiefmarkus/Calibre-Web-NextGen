import type { FoliateAnnotation } from '../FoliateEngine';

export const HIGHLIGHT_COLORS = ['yellow', 'green', 'blue', 'red'] as const;
export type HighlightColor = typeof HIGHLIGHT_COLORS[number];

const DISPLAY_COLORS = ['yellow', 'green', 'blue', 'red', 'pink', 'grey'] as const;
export type AnnotationDisplayColor = typeof DISPLAY_COLORS[number] | 'unknown';

export type ServerAnnotation = {
  annotation_id: string;
  cfi_range: string | null;
  highlighted_text: string | null;
  highlight_color: string | null;
  note_text: string | null;
  position_type?: string | null;
  source?: string | null;
  origin_device_id?: string | null;
};

export type AnnotationEditorState =
  | { mode: 'create'; selection: { value: string; text: string }; color: HighlightColor; note: string; focusNote: boolean }
  | { mode: 'edit'; annotation: FoliateAnnotation; color: HighlightColor; note: string; focusNote: boolean }
  | { mode: 'standalone'; annotation?: FoliateAnnotation; color: HighlightColor; note: string; focusNote: boolean };

export function annotationColor(value?: string | null): HighlightColor {
  return HIGHLIGHT_COLORS.includes(value as HighlightColor) ? value as HighlightColor : 'yellow';
}

export function annotationDisplayColor(value?: string | null): AnnotationDisplayColor {
  const normalized = String(value ?? '').trim().toLowerCase();
  return DISPLAY_COLORS.includes(normalized as typeof DISPLAY_COLORS[number])
    ? normalized as typeof DISPLAY_COLORS[number]
    : 'unknown';
}
