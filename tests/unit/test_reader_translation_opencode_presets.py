# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_opencode_zen_and_go_presets_enable_model_discovery():
    source = (ROOT / "frontend/src/pages/reader/translation/ReaderTranslationSettings.tsx").read_text()
    assert "label: 'OpenCode Zen'" in source
    assert "base_url: 'https://opencode.ai/zen/v1'" in source
    assert "label: 'OpenCode Go'" in source
    assert "base_url: 'https://opencode.ai/zen/go/v1'" in source
    assert source.count("discover: true") >= 2
    assert "modelsMutation.mutateAsync(savedProfile.id)" in source


def test_opencode_model_families_select_the_documented_protocol():
    source = (ROOT / "frontend/src/pages/reader/translation/ReaderTranslationSettings.tsx").read_text()
    assert source.count("if (id.startsWith('gpt-')) return 'responses';") == 2
    assert "id.startsWith('claude-') || id.startsWith('qwen')" in source
    assert "return `models/${model.trim()}:generateContent`;" in source
    assert "id.startsWith('minimax-') || id.startsWith('qwen')" in source


def test_nvidia_nim_preset_uses_public_model_discovery():
    source = (ROOT / "frontend/src/pages/reader/translation/ReaderTranslationSettings.tsx").read_text()
    assert "label: 'NVIDIA NIM'" in source
    assert "base_url: 'https://integrate.api.nvidia.com/v1'" in source
    assert "model: 'nvidia/nemotron-3-nano-30b-a3b'" in source
