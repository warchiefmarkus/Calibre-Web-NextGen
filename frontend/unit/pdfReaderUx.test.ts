import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const reader = readFileSync(new URL('../src/pages/PdfReader.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/pages/PdfReader.module.css', import.meta.url), 'utf8');

test('PDF reader fullscreen targets the complete reader shell', () => {
  assert.match(reader, /const shellRef = useRef<HTMLDivElement>\(null\)/);
  assert.match(reader, /useReaderFullscreen\(shellRef\)/);
  assert.match(reader, /<div ref=\{shellRef\} className=\{styles\.shell\}>/);
  assert.match(reader, /isFullscreen \? t\('Exit full screen'\) : t\('Full screen'\)/);
});

test('PDF reader owns visible page navigation and removes the stock overlay', () => {
  assert.match(reader, /disableOverlay\('page-controls'\)/);
  assert.match(reader, /onClick=\{\(\) => goToPage\(currentPage - 1\)\}/);
  assert.match(reader, /onClick=\{\(\) => goToPage\(currentPage \+ 1\)\}/);
  assert.match(reader, /event\.key === 'PageUp'/);
  assert.match(reader, /event\.key === 'PageDown'/);
  assert.match(css, /\.pageNavButton\s*\{/);
  assert.match(css, /\.pageNavValue\s*\{/);
});

test('PDF save indicator distinguishes debounce from an in-flight save', () => {
  assert.match(reader, /const positionPending = saveState === 'pending'/);
  assert.match(reader, /const syncBusy = pendingAnnotationSaves > 0 \|\| saveState === 'saving'/);
  assert.match(reader, /positionPending \? t\('Pending'\) : t\('Synced'\)/);
});
