"""Regression pins for stable unified-reader viewport geometry."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
BOTTOM = (ROOT / "frontend/src/pages/reader/ReaderBottomBar.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/src/pages/Reader.module.css").read_text(encoding="utf-8")


def test_progress_bar_is_always_rendered_without_visibility_state():
    assert '<footer className={styles.bottomBar}>' in BOTTOM
    assert "chromeVisible" not in READER
    assert "showChromeTemporarily" not in READER
    assert "data-chrome-visible" not in READER


def test_reader_keeps_fixed_footer_space_on_desktop_and_mobile():
    assert "grid-template-rows: 66px minmax(0, 1fr) 64px" in CSS
    assert "grid-template-rows: 66px minmax(0, 1fr) calc(76px + env(safe-area-inset-bottom, 0px))" in CSS
    assert "height: 100dvh" in CSS
    assert ".chromeHidden" not in CSS
    assert "grid-template-rows 180ms" not in CSS


def test_page_navigation_no_longer_triggers_reader_reflow_timer():
    assert "READER_CHROME_HIDE_MS" not in READER
    assert "setChromeVisible" not in READER
    assert "chromeHideTimerRef" not in READER
