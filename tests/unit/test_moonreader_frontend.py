# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "frontend/src/pages/MoonReaderSync.tsx").read_text(encoding="utf-8")
QUERIES = (ROOT / "frontend/src/lib/queries.ts").read_text(encoding="utf-8")
API = (ROOT / "frontend/src/lib/api.ts").read_text(encoding="utf-8")
ROUTES = (ROOT / "frontend/src/lib/routes.ts").read_text(encoding="utf-8")
APP = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
ACCOUNT = (ROOT / "frontend/src/pages/Account.tsx").read_text(encoding="utf-8")


def test_moonreader_settings_page_is_routed_and_linked():
    assert "moonReader: '/account/moonreader'" in ROUTES
    assert "SPA_ROUTES.moonReader" in APP
    assert "<MoonReaderSync />" in APP
    assert 'href="/account/moonreader"' in ACCOUNT


def test_moonreader_ui_never_roundtrips_cleartext_password():
    assert "password_configured" in API
    assert "password_encrypted" not in API
    assert "Configured — leave blank to keep" in PAGE
    assert "password ? { password } : {}" in PAGE
    assert "The WebDAV password is encrypted on the server" in PAGE


def test_discovered_paths_are_disabled_while_a_custom_path_is_entered():
    assert "const discoveryListDisabled = Boolean(normalizedCachePath) && !selectedDiscoveredPath" in PAGE
    assert "aria-disabled={discoveryListDisabled}" in PAGE
    assert "disabled={discoveryListDisabled}" in PAGE
    assert "styles.discoveryPanelDisabled" in PAGE


def test_moonreader_sync_status_polls_and_surfaces_summary():
    assert "status === 'queued' || status === 'running'" in QUERIES
    assert "'/api/v1/account/moonreader/sync'" in QUERIES
    assert "Syncing positions…" in PAGE
    assert "summary?.matched" in PAGE
    assert "summary?.downloaded" in PAGE
    assert "summary?.uploaded" in PAGE


def test_moonreader_folder_discovery_requires_an_explicit_selection():
    assert "useDiscoverMoonReaderCaches" in QUERIES
    assert "'/api/v1/account/moonreader/discover'" in QUERIES
    assert "MoonReaderDiscoveryResult" in API
    assert "Find Moon sync files" in PAGE
    assert "discoverCaches.data.locations.map" in PAGE
    assert 'type="radio" name="moon-cache-location"' in PAGE
    assert "!cachePath.trim()" in PAGE
    assert "select one discovered Moon+ folder" in PAGE
