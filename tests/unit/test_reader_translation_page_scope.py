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
    assert "translationPageIndex + 1" in READER
    assert ".translationPagerViewport" in CSS
    assert "overflow: hidden" in CSS
    assert "column-fill: auto" in CSS


def test_translation_boundary_turns_the_original_page_and_lands_on_cached_edge():
    assert "translationLandingRef" in READER
    assert "direction === 'next' ? 'first' : 'last'" in READER
    assert "translationTransitionRef.current = true" in READER
    assert "void navigate(direction)" in READER
    assert "landing === 'last'" in READER
