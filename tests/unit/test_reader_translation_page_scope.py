# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
TRANSLATION = (ROOT / "frontend/src/pages/reader/translation/translationPage.tsx").read_text(encoding="utf-8")
SIDE_PANEL = (ROOT / "frontend/src/pages/reader/ReaderSidePanel.tsx").read_text(encoding="utf-8")
SETTINGS_PANEL = (ROOT / "frontend/src/pages/reader/settings/ReaderSettingsPanel.tsx").read_text(encoding="utf-8")
TOOLBAR = (ROOT / "frontend/src/pages/reader/ReaderToolbar.tsx").read_text(encoding="utf-8")
OVERLAY = (ROOT / "frontend/src/pages/reader/translation/TranslationOverlay.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/src/pages/Reader.module.css").read_text(encoding="utf-8")


def test_paginated_extractor_uses_iframe_outer_coordinates_not_full_section_width():
    assert "const MAX_VISIBLE_TRANSLATION_CHARS = 8_000;" in TRANSLATION
    assert "const frameRect = frame.getBoundingClientRect();" in TRANSLATION
    assert "const pageWidth = Math.max(1, doc.documentElement.getBoundingClientRect().width);" in TRANSLATION
    assert "const visibleWidth = Math.min(rendererRect.width, pageWidth * maxColumns);" in TRANSLATION
    assert "viewport.frameRect.left + rect.left" in TRANSLATION
    assert "doc.defaultView?.innerWidth" not in TRANSLATION.split("function extractVisiblePage", 1)[1].split("function looksLikeUkrainian", 1)[0]


def test_auto_translation_skips_matching_language_and_dedupes_settling_relocates():
    assert "sourceAlreadyMatchesTarget(settings, bookLanguage" in READER
    assert "looksLikeUkrainian" in TRANSLATION
    assert "translationInFlightKeyRef.current === key" in READER
    assert "TRANSLATION_DEBOUNCE_MS = 500" in TRANSLATION
    assert "The book is already in the target language." in READER


def test_original_page_remains_visible_while_translation_is_pending():
    assert "const translationOverlayVisible = translationRequested" in READER
    assert "&& translationSegments.length > 0" in READER
    assert "translationSpinner" not in READER
    assert ".translationSpinner" not in CSS


def test_reader_content_is_clipped_above_an_opaque_bottom_bar():
    stage = CSS.split(".stage {", 1)[1].split("}", 1)[0]
    bottom = CSS.rsplit(".bottomBar {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in stage
    assert "contain: layout paint" in stage
    assert "position: relative" in bottom
    assert "z-index:" in bottom
    assert "isolation: isolate" in bottom
    assert "background: var(--reader-bg)" in bottom


def test_translation_overlay_reuses_computed_original_typography():
    assert "function computedTranslationStyle" in TRANSLATION
    for property_name in (
        "fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight",
        "letterSpacing", "textAlign", "textIndent", "textTransform",
    ):
        assert property_name in TRANSLATION
    assert "style: block.style" in OVERLAY


def test_translation_is_paginated_horizontally_without_vertical_scroll():
    assert "translationPagerContentRef" in READER
    assert "columnWidth" in OVERLAY
    assert "columnGap" in OVERLAY
    assert "translate3d" in OVERLAY
    assert "-props.pageIndex * (" in OVERLAY
    assert ".translationPagerViewport" in CSS
    assert "overflow: hidden" in CSS
    assert "column-fill: auto" in CSS


def test_translation_boundary_turns_the_original_page_and_lands_on_cached_edge():
    assert "translationLandingRef" in READER
    assert "direction === 'next' ? 'first' : 'last'" in READER
    assert "translationTransitionRef.current = true" in READER
    assert "void navigate(direction)" in READER
    assert "landing === 'last'" in READER


def test_no_visible_text_finishes_translation_transition_so_reader_can_keep_paging():
    branch = READER.split("if (!extraction) {", 1)[1].split("const { styles: sourceStyles", 1)[0]
    assert "translationTransitionRef.current = false" in branch
    assert "translationLandingRef.current = null" in branch
    assert "setTranslationBlocks([])" in branch
    assert "setTranslationSegments([])" in branch
    assert "setTranslationLayout(null)" in branch
    assert "No visible text was found on this page." in branch


def test_translation_activity_uses_the_toolbar_icon_ring():
    assert "translationOverlayBadge" not in READER
    assert ".translationOverlayBadge" not in CSS
    assert "const translationActivity = translationRequested" in READER
    assert "&& (translationLoading || translationPreloading)" in READER
    assert "aria-busy={props.translationActivity}" in TOOLBAR
    assert "data-translation-activity={props.translationLoading" in TOOLBAR
    assert "styles.translationToggleIconBusy" in TOOLBAR
    assert ".translationToggleIconBusy::after" in CSS
    assert "animation: translation-ring-spin" in CSS
    assert "translationSpinner" not in READER
    assert ".translationSpinner" not in CSS


def test_translation_settings_have_a_dedicated_side_panel():
    assert "type ReaderPanel = 'toc' | 'search' | 'bookmarks' | 'notes' | 'settings' | 'translation' | null" in SIDE_PANEL
    assert "props.panel === 'translation'" in SIDE_PANEL
    assert "title={t('Page translation')}" in TOOLBAR
    assert 'ReaderTranslationSettings' not in SETTINGS_PANEL
    assert 'ReaderTranslationSettings' in SIDE_PANEL


def test_translated_text_selection_uses_readable_yellow_highlight():
    assert '.translationPagerContent ::selection' in CSS
    selection = CSS.split('.translationPagerContent ::selection {', 1)[1].split('}', 1)[0]
    assert 'rgba(255, 214, 64' in selection
    assert 'color: #1f1f1f' in selection


def test_selected_text_can_be_temporarily_replaced_without_saving_mutated_cfi():
    assert 'const translateSelectedText = async () =>' in READER
    assert "blocks: [{ id: 'selection', tag: 'span', text: selection.text }]" in READER
    assert 'const original = range.extractContents();' in READER
    assert "marker.setAttribute('data-reader-inline-translation', 'true')" in READER
    assert 'inlineTranslationPatchesRef.current.push({ marker, original });' in READER
    assert 'if (inlineTranslationPatchesRef.current.length) {' in READER
    assert 'if (sameLogicalPage) return;' in READER
    assert 'restoreInlineTranslations();' in READER


def test_inline_translation_preserves_selected_boundary_whitespace():
    assert "leadingWhitespace: rawText.match(/^\\s+/u)?.[0] ?? ''" in READER
    assert "trailingWhitespace: rawText.match(/\\s+$/u)?.[0] ?? ''" in READER
    assert "`${selection.leadingWhitespace}${translated}${selection.trailingWhitespace}`" in READER


def test_page_translation_toggle_is_icon_only_and_has_t_hotkey():
    toggle = TOOLBAR.split('className={styles.translationToggle}', 1)[1].split('</div>', 1)[0]
    assert '<BookOpen size={16}' in toggle
    assert '<Languages size={16}' in toggle
    assert ">{t('Original')}<" not in toggle
    assert ">{t('Translation')}<" not in toggle
    assert "event.key.toLowerCase() === 't'" in READER
    assert "isReaderTypingTarget(event.target)" in READER
    css = CSS.split('.translationToggle {', 1)[1].split('.translationStatus {', 1)[0]
    assert 'border-radius: 999px' in css
    assert 'grid-template-columns: repeat(2, 44px)' in css
