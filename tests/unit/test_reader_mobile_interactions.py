# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
CSS = (ROOT / "frontend/src/pages/Reader.module.css").read_text()


def test_open_side_panel_intercepts_outside_page_turn_taps():
    assert "styles.sidePanelBackdrop" in READER
    assert "onClick={closePanel}" in READER
    assert "navigateReaderOrClosePanel('left')" in READER
    assert "navigateReaderOrClosePanel('right')" in READER
    assert ".sidePanelBackdrop" in CSS
    assert "z-index: 1001" in CSS
    assert "z-index: 1002" in CSS


def test_mobile_page_turn_zones_are_half_width():
    mobile = CSS.split("@media (max-width: 760px)", 1)[1]
    assert ".tapZone { width: clamp(44px, 14vw, 64px); }" in mobile


def test_mobile_selection_menu_tracks_touch_and_selection_changes():
    assert "doc.addEventListener('touchend'" in READER
    assert "doc.addEventListener('contextmenu'" in READER
    assert "doc.addEventListener('selectionchange'" in READER
    mobile = CSS.split("@media (max-width: 760px)", 1)[1]
    assert "bottom: 80px" in mobile
    assert "display: grid" in mobile
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in mobile
