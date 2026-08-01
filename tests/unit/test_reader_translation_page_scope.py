# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/src/pages/Reader.module.css").read_text(encoding="utf-8")


def test_paginated_extractor_uses_iframe_outer_coordinates_not_full_section_width():
    assert "const MAX_VISIBLE_TRANSLATION_CHARS = 8_000;" in READER
    assert "const frameRect = frame.getBoundingClientRect();" in READER
    assert "const pageWidth = Math.max(1, doc.documentElement.getBoundingClientRect().width);" in READER
    assert "const visibleWidth = Math.min(rendererRect.width, pageWidth * maxColumns);" in READER
    assert "viewport.frameRect.left + rect.left" in READER
    assert "doc.defaultView?.innerWidth" not in READER.split("function extractVisiblePage", 1)[1].split("function looksLikeUkrainian", 1)[0]


def test_auto_translation_skips_matching_language_and_dedupes_settling_relocates():
    assert "sourceAlreadyMatchesTarget(settings, bookLanguage" in READER
    assert "looksLikeUkrainian" in READER
    assert "translationInFlightKeyRef.current === key" in READER
    assert "TRANSLATION_DEBOUNCE_MS = 500" in READER
    assert "The book is already in the target language." in READER


def test_original_page_remains_visible_while_translation_is_pending():
    assert "const translationOverlayVisible = translationRequested" in READER
    assert "&& translationBlocks.length > 0" in READER
    assert "className={styles.translationStatus}" in READER
    assert ".translationStatus" in CSS


def test_translation_overlay_reuses_computed_original_typography():
    assert "function computedTranslationStyle" in READER
    for property_name in (
        "fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight",
        "letterSpacing", "textAlign", "textIndent", "textTransform",
    ):
        assert property_name in READER
    assert "style: block.style" in READER


def test_translation_is_paginated_horizontally_without_vertical_scroll():
    assert "translationPagerContentRef" in READER
    assert "columnWidth" in READER
    assert "columnGap" in READER
    assert "translate3d" in READER
    assert "-translationPageIndex * (" in READER
    assert ".translationPagerViewport" in CSS
    assert "overflow: hidden" in CSS
    assert "column-fill: auto" in CSS


def test_translation_boundary_turns_the_original_page_and_lands_on_cached_edge():
    assert "translationLandingRef" in READER
    assert "direction === 'next' ? 'first' : 'last'" in READER
    assert "translationTransitionRef.current = true" in READER
    assert "void navigate(direction)" in READER
    assert "landing === 'last'" in READER


def test_translation_chrome_is_compact_and_has_no_cache_badge():
    assert "translationOverlayBadge" not in READER
    assert ".translationOverlayBadge" not in CSS
    assert "<span>{t('Translation')}</span>" in READER
    status = CSS.split('.translationStatus {', 1)[1].split('}', 1)[0]
    assert 'inset-block-start: 50%' in status
    assert 'translate(-50%, -50%)' in status


def test_translation_settings_have_a_dedicated_side_panel():
    assert "type ReaderPanel = 'toc' | 'search' | 'bookmarks' | 'notes' | 'settings' | 'translation'" in READER
    assert "panel === 'translation'" in READER
    assert "translation: t('Page translation')" in READER
    settings_panel = READER.split('function ReaderSettingsPanel', 1)[1]
    assert 'ReaderTranslationSettings' not in settings_panel


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
