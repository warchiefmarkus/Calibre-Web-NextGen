# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
AI = (ROOT / "frontend/src/pages/AiSearch.tsx").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "frontend/src/components/Sidebar.tsx").read_text(encoding="utf-8")
TRANSLATION = (
    ROOT / "frontend/src/pages/reader/translation/ReaderTranslationSettings.tsx"
).read_text(encoding="utf-8")


def _manager_source() -> str:
    return TRANSLATION.split("export function LlmProfileSettings", 1)[1].split(
        "export function ReaderTranslationSettings", 1
    )[0]


def _reader_source() -> str:
    return TRANSLATION.split("export function ReaderTranslationSettings", 1)[1]
def test_sidebar_and_page_are_named_llm_with_two_tabs():
    assert "<span>{t('LLM')}</span>" in SIDEBAR
    assert "<span>{t('RAG search')}</span>" not in SIDEBAR
    assert "activeTab === 'rag'" in AI
    assert "activeTab === 'settings'" in AI
    assert "<LlmProfileSettings settings={llmReaderSettings}" in AI


def test_llm_settings_manage_profiles_as_a_list_not_a_combo():
    manager = _manager_source()
    assert "styles.translationProfileList" in manager
    assert "profiles.map((profile)" in manager
    assert "testListedProfile(profile)" in manager
    assert "removeProfile(profile)" in manager
    assert "styles.translationProfileEditor" in manager
    assert "checkAllModels()" in manager
    assert "t('Prompt')" in manager


def test_reader_translation_panel_is_compact_and_profile_only():
    reader = _reader_source()
    for label in ("Auto", "Structure", "Cache", "Next page", "From", "To", "LLM Profile"):
        assert f"t('{label}')" in reader
    assert "translationProfileList" not in reader
    assert "translationProfileEditor" not in reader
    assert "checkAllModels" not in reader
    assert "t('Prompt')" not in reader
