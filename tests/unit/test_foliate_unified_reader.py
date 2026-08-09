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
    paginator = (vendor / 'paginator.js').read_text()
    assert "minmax(0, 1fr)" in paginator
    assert "minmax(var(--_margin), 1fr)" not in paginator


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


def test_book_detail_cover_opens_primary_reader_without_hiding_cover_edit():
    detail = (ROOT / 'frontend/src/pages/BookDetail.tsx').read_text()
    css = (ROOT / 'frontend/src/pages/BookDetail.module.css').read_text()
    assert 'data-testid="book-cover-read"' in detail
    assert 'onClick={() => primaryReadTarget && navigate(primaryReadTarget)}' in detail
    assert 'disabled={!primaryReadTarget}' in detail
    assert 'className={styles.changeCover}' in detail
    assert '.coverReadHint' not in css
    assert '.coverWrap:hover .changeCover' not in css
    assert '.coverReadButton:focus-visible' in css


def test_foliate_position_saver_sends_visible_text_anchor_for_moon_mapping():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    progress = (ROOT / "frontend/src/lib/readerProgress.ts").read_text(encoding="utf-8")
    assert "detail.range?.toString()" in reader
    assert "schedulePosition(detail.cfi, detail.fraction ?? 0, anchorText" in reader
    assert "position_anchor: pending.anchorText || undefined" in progress


def test_foliate_restores_moon_position_by_text_anchor_not_moon_percentage_fraction():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    queries = (ROOT / "frontend/src/lib/queries.ts").read_text(encoding="utf-8")
    assert "position_source?: 'moonreader' | 'calibre_web'" in queries
    assert "position_anchor?: string | null" in queries
    assert "findMoonAnchorCfi" in reader
    assert "view, moonAnchor, positionQuery.data?.position_chapter" in reader
    assert "if (moonCfi) await view.goTo(moonCfi)" in reader
    assert "detail.cfi && !restoringInitialPosition" in reader
    assert "await view.init({ showTextStart: true })" in reader


def test_foliate_does_not_persist_programmatic_restore_without_user_movement():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    assert "const readingMovementRef = useRef(false);" in reader
    assert "detail.cfi && !restoringInitialPosition && readingMovementRef.current" in reader
    assert reader.count("readingMovementRef.current = false;") >= 2
    assert "const markReadingMovement = useCallback" in reader
    assert "markReadingMovement();" in reader
    assert "doc.addEventListener('wheel', armMovement" in reader
    assert "doc.addEventListener('keydown', armMovement);" in reader
