from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_reader_extracts_and_renders_safe_structured_inline_runs():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    queries = (ROOT / "frontend/src/lib/queries.ts").read_text()
    assert "ReaderTranslationRunMark" in queries
    assert "function inlineRunContext" in reader
    assert "visibleBlockRuns" in reader
    assert "marks: context.marks" in reader
    assert "let pendingSpace = false" in reader
    assert "const prefix = pendingSpace && runs.length ? ' ' : ''" in reader
    assert "runs: extracted.requestRuns" in reader
    assert "sourceRuns: sourceRuns[block.id]" in reader
    assert "translatedInlineContent(block)" in reader
    assert "safeTranslationHref" in reader
    assert "javascript:" not in reader[reader.index("function safeTranslationHref"):reader.index("function translatedInlineContent")]


def test_model_never_receives_source_link_urls_or_html():
    service = (ROOT / "cps/services/reader_translation.py").read_text()
    assert "do not emit HTML, XML, Markdown, CSS, URLs" in service
    assert "_model_block_payload" in service
    payload = service[service.index("def _model_block_payload"):service.index("def _chat_payload")]
    assert "href" not in payload
