# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_full_page_cache_has_a_persisted_reader_checkbox():
    settings = (ROOT / "cps/reader_settings.py").read_text()
    queries = (ROOT / "frontend/src/lib/queries.ts").read_text()
    panel = (ROOT / "frontend/src/pages/reader/translation/ReaderTranslationSettings.tsx").read_text()
    assert '"translationCacheEnabled": True' in settings
    assert 'translationCacheEnabled: boolean;' in queries
    assert "Cache full translated pages" in panel
    assert 'checked={settings.translationCacheEnabled}' in panel


def test_full_page_cache_can_be_bypassed_but_inline_snippets_are_never_persisted():
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text()
    queries = (ROOT / "frontend/src/lib/queries.ts").read_text()
    assert 'cache_enabled: boolean;' in queries
    assert 'cache_enabled: settings.translationCacheEnabled' in reader
    assert 'cache_enabled: false' in reader
    assert 'settings.translationCacheEnabled\n        ? translationCacheRef.current.get(key)' in reader
    assert 'if (settings.translationCacheEnabled)' in reader
