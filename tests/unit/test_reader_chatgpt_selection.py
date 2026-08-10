# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
SELECTION = (ROOT / "frontend/src/pages/reader/SelectionActions.tsx").read_text()
CSS = (ROOT / "frontend/src/pages/Reader.module.css").read_text()


def test_selection_toolbar_opens_chatgpt_with_the_exact_selected_text():
    assert "function chatGptSelectedTextUrl" in READER
    assert "https://chatgpt.com/?q=${encodeURIComponent(text.trim())}" in READER
    assert "window.open(chatGptSelectedTextUrl(text), '_blank', 'noopener,noreferrer')" in READER
    assert "const text = pendingSelection?.text.trim()" in READER


def test_selection_toolbar_uses_the_font_awesome_openai_icon():
    assert "FontAwesomeIcon" in SELECTION
    assert "faOpenai" in SELECTION
    toolbar = SELECTION.split('className={styles.selectionBar}', 1)[1]
    assert "props.openChatGpt" in toolbar
    assert "Open selected text in ChatGPT" in toolbar
    assert "styles.selectionChatGptIcon" in toolbar
    assert "ChatGPT" in toolbar
    assert ".selectionChatGptIcon" in CSS
