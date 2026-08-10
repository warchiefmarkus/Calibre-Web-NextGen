import type { FoliateAnnotation } from '../FoliateEngine';

export const HIGHLIGHT_COLORS = ['yellow', 'green', 'blue', 'red'] as const;
export type HighlightColor = typeof HIGHLIGHT_COLORS[number];

export type ServerAnnotation = {
  annotation_id: string;
  cfi_range: string | null;
  highlighted_text: string | null;
  highlight_color: string | null;
  note_text: string | null;
  position_type?: string | null;
};

export type AnnotationEditorState =
  | { mode: 'create'; selection: { value: string; text: string }; color: HighlightColor; note: string; focusNote: boolean }
  | { mode: 'edit'; annotation: FoliateAnnotation; color: HighlightColor; note: string; focusNote: boolean }
  | { mode: 'standalone'; annotation?: FoliateAnnotation; color: HighlightColor; note: string; focusNote: boolean };

export function annotationColor(value?: string | null): HighlightColor {
  return HIGHLIGHT_COLORS.includes(value as HighlightColor) ? value as HighlightColor : 'yellow';
}
