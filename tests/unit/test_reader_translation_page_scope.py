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
    assert "doc.defaultView?.innerWidth" not in READER.split("function extractVisiblePageBlocks", 1)[1].split("function translationPageKey", 1)[0]


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
