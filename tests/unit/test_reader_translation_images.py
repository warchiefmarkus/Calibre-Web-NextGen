# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_visible_images_are_preserved_as_local_overlay_segments():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    translation = (ROOT / "frontend/src/pages/reader/translation/translationPage.tsx").read_text()
    overlay = (ROOT / "frontend/src/pages/reader/translation/TranslationOverlay.tsx").read_text()
    css = (ROOT / "frontend/src/pages/Reader.module.css").read_text()
    assert "TRANSLATION_CONTENT_SELECTOR" in translation
    assert "preservedImageSegment" in translation
    assert "element.localName.toLowerCase() === 'img'" in translation
    assert "data-source-image-id" in overlay
    assert "src={segment.src}" in overlay
    assert "translationImage" in css
    assert "break-inside: avoid-column" in css


def test_images_are_not_added_to_the_llm_request_blocks():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    translation = (ROOT / "frontend/src/pages/reader/translation/translationPage.tsx").read_text()
    start = translation.index("function extractVisiblePage")
    end = translation.index("function looksLikeUkrainian")
    extraction = translation[start:end]
    assert "blocks.push({" in extraction and "runs: extracted.requestRuns" in extraction
    assert "segments.push(image)" in extraction
    assert "blocks.push(image)" not in extraction
    assert "cache_enabled: settings.translationCacheEnabled" in reader


def test_image_only_pages_can_render_without_an_llm_request():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    assert "if (!blocks.length)" in reader
    assert "setTranslationSegments(segments)" in reader
    assert "translationSegments.length > 0" in reader
