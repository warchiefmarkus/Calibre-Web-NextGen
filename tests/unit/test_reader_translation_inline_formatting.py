from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_reader_extracts_and_renders_safe_structured_inline_runs():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    translation = (ROOT / "frontend/src/pages/reader/translation/translationPage.tsx").read_text()
    overlay = (ROOT / "frontend/src/pages/reader/translation/TranslationOverlay.tsx").read_text()
    queries = (ROOT / "frontend/src/lib/queries.ts").read_text()
    assert "ReaderTranslationRunMark" in queries
    assert "function inlineRunContext" in translation
    assert "visibleBlockRuns" in translation
    assert "marks: context.marks" in translation
    assert "let pendingSpace = false" in translation
    assert "const prefix = pendingSpace && runs.length ? ' ' : ''" in translation
    assert "runs: extracted.requestRuns" in translation
    assert "sourceRuns: sourceRuns[block.id]" in reader
    assert "translatedInlineContent(block)" in overlay
    assert "safeTranslationHref" in translation
    assert "javascript:" not in translation[translation.index("function safeTranslationHref"):translation.index("function translatedInlineContent")]


def test_model_never_receives_source_link_urls_or_html():
    service = (ROOT / "cps/services/reader_translation.py").read_text()
    assert "do not emit HTML, XML, Markdown, CSS, URLs" in service
    assert "_model_block_payload" in service
    payload = service[service.index("def _model_block_payload"):service.index("def _chat_payload")]
    assert "href" not in payload
