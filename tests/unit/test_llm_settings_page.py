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
QUERIES = (ROOT / "frontend/src/lib/queries.ts").read_text(encoding="utf-8")


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


def test_llm_profiles_and_model_catalog_are_visually_distinct():
    manager = _manager_source()
    assert "styles.translationProfileList" in manager
    assert "removeProfile(profile)" in manager
    assert "styles.translationModelCatalogIdentity" in manager
    assert "t('Models')} · {selectedProfile.name}" in manager
    assert "BrandMark" in manager


def test_model_editor_is_plain_text_and_catalog_owns_discovery_selection():
    manager = _manager_source()
    assert 'list="reader-translation-models"' not in manager
    assert '<datalist id="reader-translation-models">' not in manager
    assert "selectCatalogModel(item.model)" in manager
    assert "modelsMutation.mutateAsync(selectedProfile.id)" in manager


def test_provider_and_model_brand_icons_use_models_dev_with_fallbacks():
    manager = _manager_source()
    assert "https://models.dev/logos/" in TRANSLATION
    assert "translationBrandFallback" in TRANSLATION
    assert "modelLogoId(item.model" in manager
    assert "providerLogoId(providerFor(profile.base_url))" in manager


def test_llm_model_browser_has_provider_and_model_columns():
    manager = _manager_source()
    assert "styles.translationProviderModelGrid" in manager
    assert "styles.translationProviderBrowser" in manager
    assert "styles.translationProviderList" in manager
    assert "selectConfiguredProvider(group.key)" in manager
    assert "styles.translationModelsBrowser" in manager
    assert "configuredProviders.map((group)" in manager


def test_model_catalog_is_explicitly_not_a_second_profile_list():
    manager = _manager_source()
    assert "t('Model catalog')" in manager
    assert "t('Provider catalogs')" in manager
    assert "Provider rows below are catalog sources, not profiles" in manager
    assert "Enter a model ID manually or select one from the model catalog" in manager


def test_opencode_cli_is_preserved_as_transport_when_catalog_model_changes():
    manager = _manager_source()
    assert "if (profile.endpoint_path === 'opencode-cli') return 'opencode-cli';" in TRANSLATION
    assert "endpointForProfileModel(" in manager
    assert "profileEndpointLabel(profile)" in manager


def test_rag_search_controls_live_in_a_separate_panel():
    assert "styles.ragSearchPanel" in AI


def test_profile_delete_removes_client_cache_immediately():
    block = QUERIES.split("export function useDeleteReaderTranslationProfile", 1)[1].split(
        "export function useTestReaderTranslationProfile", 1
    )[0]
    assert "onSuccess: (_data, id)" in block
    assert "current.profiles.filter((profile) => profile.id !== id)" in block
    assert "invalidateQueries({ queryKey: readerTranslationProfilesKey })" in block
