# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_visible_images_are_preserved_as_local_overlay_segments():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    css = (ROOT / "frontend/src/pages/Reader.module.css").read_text()
    assert "TRANSLATION_CONTENT_SELECTOR" in reader
    assert "preservedImageSegment" in reader
    assert "element.localName.toLowerCase() === 'img'" in reader
    assert "data-source-image-id" in reader
    assert "src={segment.src}" in reader
    assert "translationImage" in css
    assert "break-inside: avoid-column" in css


def test_images_are_not_added_to_the_llm_request_blocks():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    start = reader.index("function extractVisiblePage")
    end = reader.index("function looksLikeUkrainian")
    extraction = reader[start:end]
    assert "blocks.push({ id: blockId" in extraction
    assert "segments.push(image)" in extraction
    assert "blocks.push(image)" not in extraction
    assert "cache_enabled: settings.translationCacheEnabled" in reader


def test_image_only_pages_can_render_without_an_llm_request():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    assert "if (!blocks.length)" in reader
    assert "setTranslationSegments(segments)" in reader
    assert "translationSegments.length > 0" in reader
