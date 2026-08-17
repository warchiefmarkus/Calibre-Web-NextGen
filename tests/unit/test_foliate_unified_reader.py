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
    assert "position_section?: number | null" in queries
    assert "positionQuery.data?.position_section ?? positionQuery.data?.position_chapter" in reader
    assert "if (moonCfi) await view.goTo(moonCfi)" in reader
    assert "detail.cfi && !restoringInitialPosition" in reader
    assert "await view.init({ showTextStart: true })" in reader


def test_foliate_does_not_persist_programmatic_restore_without_user_movement():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    movement = (ROOT / "frontend/src/pages/reader/useReadingMovement.ts").read_text(encoding="utf-8")
    assert "useReadingMovement()" in reader
    assert "detail.cfi && !restoringInitialPosition && readingMovementRef.current" in reader
    assert reader.count("resetReadingMovement();") >= 2
    assert "movementRef = useRef(false)" in movement
    assert "const markReadingMovement = useCallback" in movement
    assert "markReadingMovement();" in reader
    assert "doc.addEventListener('wheel', armMovement" in reader
    assert "doc.addEventListener('touchmove', armMovement" in reader
    assert "doc.addEventListener('pointermove', armPointerDrag" in reader
    assert "doc.addEventListener('pointerdown', armMovement" not in reader
    assert "doc.addEventListener('touchstart', armMovement" not in reader
    assert "doc.addEventListener('keydown', armMovement);" in reader


def test_foliate_uses_canonical_text_progress_but_restores_own_position_by_cfi():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    progress = (ROOT / "frontend/src/lib/readerProgress.ts").read_text(encoding="utf-8")
    assert "const savedFoliateCfi = savedLocator?.startsWith('epubcfi(')" in reader
    assert "const fallbackLocation = savedFoliateCfi" in reader
    assert "savedPositionFraction ?? positionQuery.data?.position_fraction" in reader
    assert "const canonical = Number(saved?.position_fraction);" in progress
    assert "setSavedPositionFraction(clampReadingFraction(canonical))" in progress

def test_foliate_supports_upstream_standalone_book_notes_without_drawing_an_anchor():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    composer = (ROOT / "frontend/src/pages/reader/annotations/AnnotationComposer.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "frontend/src/pages/reader/annotations/AnnotationPanel.tsx").read_text(encoding="utf-8")
    assert "position_type: 'unanchored'" in reader
    assert "if (!annotation.unanchored) void view.addAnnotation(annotation);" in reader
    assert "if (!annotation.unanchored) await viewRef.current?.deleteAnnotation(annotation);" in reader
    assert "openStandaloneNote" in reader
    assert "t('Write a note')" in composer
    assert "t('Write a note')" in panel



def test_reader_annotation_ux_is_modular_and_matches_upstream_capabilities():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    composer = (ROOT / "frontend/src/pages/reader/annotations/AnnotationComposer.tsx").read_text(encoding="utf-8")
    types = (ROOT / "frontend/src/pages/reader/annotations/types.ts").read_text(encoding="utf-8")
    panel = (ROOT / "frontend/src/pages/reader/ReaderSidePanel.tsx").read_text(encoding="utf-8")
    assert "window.prompt" not in reader
    assert "window.prompt" not in composer
    assert "['yellow', 'green', 'blue', 'red']" in types
    assert "<textarea" in composer
    assert "useFocusTrap(dialogRef" in composer
    assert "useFocusTrap(panelRef" in panel
    assert "highlight_color: color" in reader
    assert "await viewRef.current?.deleteAnnotation(existing)" in reader
    assert "await viewRef.current?.addAnnotation(updated)" in reader


def test_reader_fullscreen_uses_feature_detection_and_webkit_fallback():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    toolbar = (ROOT / "frontend/src/pages/reader/ReaderToolbar.tsx").read_text(encoding="utf-8")
    fullscreen = (ROOT / "frontend/src/pages/reader/fullscreen.ts").read_text(encoding="utf-8")
    assert "useReaderFullscreen(shellRef)" in reader
    assert "props.fullscreenSupported &&" in toolbar
    assert "<Minimize" in toolbar and "<Maximize" in toolbar
    assert "webkitRequestFullscreen" in fullscreen
    assert "webkitExitFullscreen" in fullscreen
    assert "webkitfullscreenchange" in fullscreen
    assert "fullscreenchange" in fullscreen


def test_reader_touch_targets_do_not_shrink_below_44px_on_mobile():
    css = (ROOT / "frontend/src/pages/Reader.module.css").read_text(encoding="utf-8")
    assert "min-width: 44px;\n  min-height: 44px;" in css
    assert ".iconButton { min-width: 44px; min-height: 44px; }" in css
    assert "inline-size: 44px;\n  block-size: 44px;" in css
    assert ".itemActions button { min-width: 44px; min-height: 44px;" in css
    assert "min-width: 34px" not in css


def test_reader_is_split_into_feature_modules():
    reader = ROOT / "frontend/src/pages/Reader.tsx"
    required = [
        "reader/FoliateEngine.ts",
        "reader/useMoonRestore.ts",
        "reader/useReadingMovement.ts",
        "reader/fullscreen.ts",
        "reader/ReaderSidePanel.tsx",
        "reader/annotations/AnnotationComposer.tsx",
        "reader/annotations/AnnotationPanel.tsx",
        "reader/translation/translationPage.tsx",
        "reader/settings/ReaderSettingsPanel.tsx",
    ]
    for relative in required:
        assert (ROOT / "frontend/src/pages" / relative).is_file(), relative
    assert len(reader.read_text(encoding="utf-8").splitlines()) < 1700


def test_annotations_load_in_parallel_and_do_not_block_reader_ready():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    start = reader.index("const annotationPromise = apiGet")
    book_fetch = reader.index("const response = await fetch", start)
    ready = reader.index("setReady(true);", book_fetch)
    hydrate = reader.index("void annotationPromise.then", ready)
    assert start < book_fetch, "annotation request should overlap book download/open"
    assert ready < hydrate, "Reader must become usable before annotation hydration completes"
    hydration = reader[hydrate:reader.index("      } catch (cause)", hydrate)]
    assert "if (!annotation.unanchored) void view.addAnnotation(annotation);" in hydration
