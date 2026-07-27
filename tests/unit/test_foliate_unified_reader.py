"""Architecture pins for the vendored unified ebook reader."""
import json
from pathlib import Path
import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_foliate_snapshot_is_pinned_and_licensed():
    vendor = ROOT / 'frontend/src/vendor/foliate-js'
    upstream = (vendor / 'UPSTREAM.md').read_text()
    assert '78914aefbb1351545fe60e4cdbabcce514d1f201' in upstream
    assert (vendor / 'LICENSE').is_file()
    assert (vendor / 'view.js').is_file()
    assert "import('./pdf.js')" not in (vendor / 'view.js').read_text()
    for name in ('paginator.js', 'fixed-layout.js'):
        source = (vendor / name).read_text()
        assert "sandbox', 'allow-same-origin'" in source
        assert "allow-same-origin allow-scripts" not in source


def test_epubjs_and_custom_fb2_parser_are_removed():
    package = json.loads((ROOT / 'frontend/package.json').read_text())
    assert 'epubjs' not in package.get('dependencies', {})
    assert not (ROOT / 'frontend/src/lib/fb2.ts').exists()
    native = (ROOT / 'frontend/src/pages/NativeReader.tsx').read_text()
    assert 'Fb2Reader' not in native
    assert "const COMIC = new Set(['cbr', 'cbt', 'cb7'])" in native


def test_format_routing_uses_one_reader_for_reflowable_formats():
    target = (ROOT / 'frontend/src/lib/readerTarget.ts').read_text()
    app = (ROOT / 'frontend/src/App.tsx').read_text()
    for fmt in ('epub', 'kepub', 'fb2', 'mobi', 'azw3', 'cbz'):
        assert f"'{fmt}'" in target
    assert 'FOLIATE_FORMATS.has' in app
    assert "? <Reader id={p.id} format={p.format} />" in app
