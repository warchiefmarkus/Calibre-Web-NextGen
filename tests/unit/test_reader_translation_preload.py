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
    assert "translationLoading || (translationPreloading && translationOverlayVisible)" in READER
    assert "? 'translation'" in READER
    assert "'preload'" in READER
    assert "aria-busy={translationActivity}" in READER


def test_aborted_joined_preload_always_releases_foreground_spinner():
    joined = READER.split("const preloadTask = translationPreloadTaskRef.current", 1)[1].split(
        "translationAbortRef.current?.abort();", 1
    )[0]
    finalizer = joined.split("} finally {", 1)[1]
    assert "translationInFlightKeyRef.current === key" in finalizer
    assert "setTranslationLoading(false)" in finalizer
    assert "if (!preloadTask.controller.signal.aborted) setTranslationLoading(false)" not in finalizer


def test_stale_activity_is_reconciled_and_foreground_errors_cancel_preload():
    assert "translationStartedAtRef" in READER
    assert "translationPreloadStartedAtRef" in READER
    assert "translationRequestTimeoutMs" in READER
    assert "Page translation timed out." in READER
    assert "cancelTranslationPreload();" in READER
    assert "translationPreloading && translationOverlayVisible" in READER
    assert "!translationError" in READER
    assert "!translationInFlightKeyRef.current" in READER
    assert "!translationPreloadInFlightKeyRef.current" in READER


def test_failed_page_waits_for_explicit_retry_instead_of_looping():
    assert "translationCurrentKeyRef" in READER
    assert "translationFailureRef" in READER
    assert "translationFailureRef.current = { key, message }" in READER
    assert "translationFailureRef.current = { key: failedKey, message }" in READER
    assert "previousFailure?.key === key" in READER
    assert "setTranslationError(previousFailure.message)" in READER
    assert "translationFailureRef.current = null" in READER
    assert "setTranslationRetry((value) => value + 1)" in READER


def test_different_foreground_page_cancels_stale_preload_before_request():
    section = READER.split("const preloadTask = translationPreloadTaskRef.current", 1)[1].split(
        "if (preloadTask?.key === key)", 1
    )[0]
    assert "preloadTask.key !== key" in section
    assert "cancelTranslationPreload();" in section
