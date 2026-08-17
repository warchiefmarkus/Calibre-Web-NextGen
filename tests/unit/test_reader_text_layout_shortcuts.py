# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
QUERIES = (ROOT / "frontend/src/lib/queries.ts").read_text()
SETTINGS = (ROOT / "frontend/src/pages/reader/settings/ReaderSettingsPanel.tsx").read_text()
STYLE = (ROOT / "frontend/src/pages/reader/settings/readerStyle.ts").read_text()
TRANSLATION = (ROOT / "frontend/src/pages/reader/translation/translationPage.tsx").read_text()
OVERLAY = (ROOT / "frontend/src/pages/reader/translation/TranslationOverlay.tsx").read_text()


def test_foliate_reader_persists_and_applies_text_justification():
    assert "justifyText: boolean;" in QUERIES
    assert "checked={settings.justifyText}" in SETTINGS
    assert "update({ justifyText: event.target.checked })" in SETTINGS
    assert "body, p, li, blockquote { text-align: justify !important;" in STYLE
    assert "text-align-last: auto !important" in STYLE
    assert "textAlign: style.textAlign" in TRANSLATION
    assert "style: block.style" in OVERLAY


def test_font_shortcuts_adjust_exactly_one_percent_and_respect_limits():
    assert "currentSettings.fontSize + 1" in READER
    assert "currentSettings.fontSize - 1" in READER
    assert "Math.min(FONT_MAX" in READER
    assert "Math.max(FONT_MIN" in READER


def test_font_shortcuts_cover_top_row_and_numeric_keypad_without_repeat_spam():
    assert "event.key === '+'" in READER
    assert "event.code === 'Equal' && event.shiftKey" in READER
    assert "event.code === 'NumpadAdd'" in READER
    assert "event.key === '-'" in READER
    assert "event.code === 'NumpadSubtract'" in READER
    assert "!event.repeat && (event.key === '+'" in READER
    assert "!event.repeat && (event.key === '-'" in READER


def test_font_shortcuts_keep_browser_zoom_and_form_controls_untouched():
    assert "event.ctrlKey || event.metaKey || event.altKey" in READER
    assert "isReaderTypingTarget(event.target)" in READER
    assert "event.preventDefault();" in READER
