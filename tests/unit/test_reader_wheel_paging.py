"""Source contract for desktop mouse-wheel page turns in foliate reader."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")


def test_wheel_paging_is_desktop_paginated_only():
    assert "settingsRef.current?.flow !== 'paginated'" in READER
    assert "'(hover: hover) and (pointer: fine)'" in READER
    assert "event.ctrlKey || event.metaKey || event.altKey || event.shiftKey" in READER


def test_wheel_paging_has_threshold_cooldown_and_direction():
    assert "READER_WHEEL_THRESHOLD_PX = 48" in READER
    assert "READER_WHEEL_COOLDOWN_MS = 320" in READER
    assert "READER_WHEEL_IDLE_RESET_MS = 160" in READER
    assert "wheelDeltaRef.current > 0 ? 'next' : 'prev'" in READER
    assert "wheelLockUntilRef.current = now + READER_WHEEL_COOLDOWN_MS" in READER


def test_wheel_listener_reaches_foliate_documents_without_hijacking_controls():
    assert "doc.addEventListener('wheel', handleReaderWheel, { passive: false })" in READER
    assert "stage.addEventListener('wheel', handleReaderWheel, { passive: false })" in READER
    assert "input, textarea, select, button" in READER
    assert "data-reader-wheel-page-zone" in READER
