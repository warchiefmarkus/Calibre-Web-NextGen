"""Unified foliate reader highlight/notes regression coverage."""
from pathlib import Path
import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / 'frontend/src/pages/Reader.tsx').read_text()
DRAW = (ROOT / 'frontend/src/pages/reader/annotations/draw.ts').read_text()
API = (ROOT / 'frontend/src/lib/api.ts').read_text()


def test_reader_uses_foliate_overlay_and_server_annotation_ids():
    assert "Overlayer.highlight" in DRAW
    assert "drawFoliateHighlight" in READER
    assert "annotationsRef.current" in READER
    assert "annotation_id" in READER
    assert "view.addAnnotation" in READER
    assert "deleteAnnotation(annotation)" in READER


def test_highlight_create_update_delete_use_server_api():
    assert "apiPost<ServerAnnotation>(`/annotations/${id}`" in READER
    assert "apiPatch<ServerAnnotation>" in READER
    assert "apiDelete(`/annotations/${id}/${encodeURIComponent(annotation.id)}?format=${encodeURIComponent(fmt)}`)" in READER
    assert "note_text" in READER
    assert "highlight_color" in READER


def test_api_helpers_support_annotation_mutations():
    assert "export async function apiDelete" in API
    assert "method: 'DELETE'" in API
    assert "export async function apiPatch" in API
    assert "method: 'PATCH'" in API
