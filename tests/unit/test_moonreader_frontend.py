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
BOOK_DETAIL = (ROOT / "frontend/src/pages/BookDetail.tsx").read_text(encoding="utf-8")
BOOK_DETAIL_CSS = (ROOT / "frontend/src/pages/BookDetail.module.css").read_text(encoding="utf-8")


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
    assert "`/api/v1/books/${bookId}/moonreader/sync`" in QUERIES
    assert "if (!status.pending)" in QUERIES
    assert "queryKey: ['book', String(bookId)]" in QUERIES
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


def test_book_detail_keeps_bidirectional_moon_sync_after_chatgpt_in_more_actions():
    chatgpt = BOOK_DETAIL.index("id: 'chatgpt-similar'")
    moon = BOOK_DETAIL.index("id: 'moonreader-sync'")
    assert moon > chatgpt
    assert "testId: 'chatgpt-similar-books'" in BOOK_DETAIL
    assert "testId: 'moonreader-book-sync'" in BOOK_DETAIL
    assert 'useStartBookMoonReaderSync(id)' in BOOK_DETAIL
    assert '<Cloud size={15}' in BOOK_DETAIL
    assert '`/api/v1/books/${bookId}/moonreader/sync`' in QUERIES
    # #2237 moved secondary book actions into one accessible gear menu; do not
    # resurrect the old square cover-page button just to keep Moon sync visible.
    assert '.moonSyncBtn' not in BOOK_DETAIL_CSS
