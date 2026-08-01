# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
READER = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
PANEL = (ROOT / "frontend/src/pages/ReaderTranslationSettings.tsx").read_text()
SETTINGS = (ROOT / "cps/reader_settings.py").read_text()


def test_preload_setting_is_persisted_and_requires_auto_translation_cache():
    assert '"translationPreloadNextPage": False' in SETTINGS
    assert 'translationPreloadNextPage: boolean;' in (ROOT / "frontend/src/lib/queries.ts").read_text()
    assert "Preload one translated page ahead" in PANEL
    assert 'checked={settings.translationPreloadNextPage}' in PANEL
    assert 'disabled={!settings.translationEnabled || !settings.translationCacheEnabled}' in PANEL


def test_next_page_is_extracted_without_moving_foliate_or_changing_cfi():
    viewport = READER.split("function canExtractTranslationPageOffset", 1)[1].split(
        "function rectIntersectsViewport", 1
    )[0]
    assert "page + pageOffset <= pages - 2" in viewport
    assert "pageOffset * Math.max(1, Number(renderer.size)" in viewport
    assert "renderer.getAttribute('dir') === 'rtl' ? -1 : 1" in viewport
    assert "extractVisiblePage(viewRef.current?.renderer, 1)" in READER
    scheduler = READER.split("const scheduleNextTranslationPreload", 1)[1].split(
        "useEffect(() => {", 1
    )[0]
    assert ".next(" not in scheduler
    assert ".prev(" not in scheduler
    assert "goTo(" not in scheduler


def test_preload_is_background_cached_deduplicated_and_silent():
    runner = READER.split("const runTranslationPreload", 1)[1].split(
        "useEffect(() => {", 1
    )[0]
    assert "cache_enabled: true" in runner
    assert "cacheTranslatedPage(job.key, response.blocks)" in runner
    assert "translationPreloadInFlightKeyRef" in runner
    assert "translationPreloadQueuedRef.current = job" in runner
    assert "setTranslationLoading" not in runner
    assert "setTranslationError" not in runner
    assert "catch(() =>" in runner
    assert "translationPreloadAbortRef.current?.abort()" in READER
    assert "currentConfigMatches" in runner


def test_preload_runs_after_current_page_cache_hit_or_translation_success():
    assert READER.count("scheduleNextTranslationPreload(settings);") >= 3
    assert "translationPreloadNextPage" in READER
    assert "activeSettings.flow !== 'paginated'" in READER
    assert "sourceAlreadyMatchesTarget(activeSettings, bookLanguage, blocks)" in READER


def test_foreground_joins_the_matching_preload_promise_without_a_second_request():
    assert "type TranslationPreloadTask" in READER
    assert "translationPreloadTaskRef.current = { key: job.key, controller, promise }" in READER
    foreground = READER.split("const preloadTask = translationPreloadTaskRef.current", 1)[1].split(
        "translationAbortRef.current?.abort();", 1
    )[0]
    assert "preloadTask?.key === key" in foreground
    assert "await preloadTask.promise" in foreground
    assert "translateReaderPage(" not in foreground
    assert "applyTranslationResponse(response)" in foreground


def test_toolbar_ring_reports_both_foreground_translation_and_preload_activity():
    assert "const [translationPreloading, setTranslationPreloading] = useState(false)" in READER
    assert "setTranslationPreloading(true)" in READER
    assert "setTranslationPreloading(false)" in READER
    assert "translationLoading || translationPreloading" in READER
    assert "? 'translation'" in READER
    assert "'preload'" in READER
    assert "aria-busy={translationActivity}" in READER
