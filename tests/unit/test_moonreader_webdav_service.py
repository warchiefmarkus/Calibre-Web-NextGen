# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_parse_moonreader_position():
    from cps.services.moonreader_webdav import parse_position
    value = parse_position("1703297605115*21@0#4826:11.1%")
    assert value.chapter == 21
    assert value.locator == "0#4826"
    assert value.percentage == 11.1
    assert value.timestamp == datetime(2023, 12, 23, 2, 13, 25, 115000, tzinfo=timezone.utc)


@pytest.mark.parametrize(("raw", "chapter", "locator", "percentage"), [
    ("1634339204311*34:15.0%", 34, "34", 15.0),
    ("1634339204311*0:100%", 0, "0", 100.0),
    ("1634339204311*0@0#0:0,00%", 0, "0#0", 0.0),
])
def test_parse_moonreader_position_supports_pdf_and_decimal_comma(
        raw, chapter, locator, percentage):
    from cps.services.moonreader_webdav import parse_position
    value = parse_position(raw)
    assert value.chapter == chapter
    assert value.locator == locator
    assert value.percentage == percentage


@pytest.mark.parametrize("value", [
    "not-a-position",
    "1703297605115*21@0#4826:101%",
    b"\xff\xfe",
])
def test_parse_moonreader_position_rejects_invalid(value):
    from cps.services.moonreader_webdav import MoonReaderError, parse_position
    with pytest.raises(MoonReaderError):
        parse_position(value)


def test_normalize_webdav_url_and_cache_path():
    from cps.services.moonreader_webdav import (
        MoonReaderError, normalize_base_url, normalize_cache_path,
    )
    assert normalize_base_url("http://host:123/books") == "http://host:123/books/"
    assert normalize_cache_path("/.Moon+/Cache/") == ".Moon+/Cache"
    with pytest.raises(MoonReaderError):
        normalize_base_url("file:///tmp/books")
    with pytest.raises(MoonReaderError):
        normalize_base_url("http://user:password@host/books")
    with pytest.raises(MoonReaderError):
        normalize_cache_path("../secrets")


def test_propfind_decodes_moon_plus_path_and_metadata():
    from cps.services.moonreader_webdav import WebDavClient
    xml = b'''<?xml version="1.0"?>
    <multistatus xmlns="DAV:">
      <response><href>/books/.Moon%2B/Cache/</href><propstat><prop>
        <resourcetype><collection/></resourcetype>
      </prop></propstat></response>
      <response><href>/books/.Moon%2B/Cache/Book.epub.po</href><propstat><prop>
        <resourcetype/><getcontentlength>34</getcontentlength>
        <getetag>"etag-1"</getetag>
        <getlastmodified>Mon, 03 Aug 2026 10:00:00 GMT</getlastmodified>
      </prop></propstat></response>
    </multistatus>'''
    response = MagicMock(status_code=207, content=xml)
    client = WebDavClient("http://host/books/", "reader", "secret")
    with patch.object(client.session, "request", return_value=response) as request:
        rows = client.propfind(".Moon+/Cache", depth=1)
    assert request.call_args.args[1] == "http://host/books/.Moon%2B/Cache/"
    assert rows[1].path == ".Moon+/Cache/Book.epub.po"
    assert rows[1].size == 34
    assert rows[1].etag == '"etag-1"'
    assert rows[1].modified == datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def test_discover_positions_requires_an_explicit_selected_path():
    from cps.services.moonreader_webdav import WebDavClient, WebDavResource
    client = WebDavClient("http://host/books/", "reader", "secret")
    position = WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False)
    with patch.object(client, "propfind", return_value=[position]) as propfind:
        assert client.discover_positions("") == ("", [], False)
        path, rows, found = client.discover_positions("Moon/.Moon+/Cache")
    assert path == "Moon/.Moon+/Cache"
    assert rows == [position]
    assert found is True
    propfind.assert_called_once_with("Moon/.Moon+/Cache", depth=1)


def test_bounded_discovery_finds_and_reports_multiple_moon_cache_locations():
    from cps.services.moonreader_webdav import WebDavClient, WebDavResource
    client = WebDavClient("http://host/books/", "reader", "secret")
    modified = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    tree = {
        "": [WebDavResource("", True), WebDavResource("Moon", True),
             WebDavResource("Apps", True), WebDavResource("Author", True)],
        "Moon": [WebDavResource("Moon", True), WebDavResource("Moon/.Moon+", True)],
        "Moon/.Moon+": [WebDavResource("Moon/.Moon+", True),
                         WebDavResource("Moon/.Moon+/Cache", True)],
        "Moon/.Moon+/Cache": [
            WebDavResource("Moon/.Moon+/Cache", True),
            WebDavResource("Moon/.Moon+/Cache/One.fb2.po", False, modified=modified),
        ],
        "Apps": [WebDavResource("Apps", True), WebDavResource("Apps/Books", True)],
        "Apps/Books": [WebDavResource("Apps/Books", True),
                         WebDavResource("Apps/Books/.Moon+", True)],
        "Apps/Books/.Moon+": [WebDavResource("Apps/Books/.Moon+", True),
                                WebDavResource("Apps/Books/.Moon+/Cache", True)],
        "Apps/Books/.Moon+/Cache": [
            WebDavResource("Apps/Books/.Moon+/Cache", True),
            WebDavResource("Apps/Books/.Moon+/Cache/Two.epub.po", False),
            WebDavResource("Apps/Books/.Moon+/Cache/notes.an", False),
        ],
        "Author": [WebDavResource("Author", True)],
    }
    with patch.object(client, "propfind", side_effect=lambda path, depth=1: tree.get(path)):
        result = client.discover_cache_locations(max_depth=5, max_collections=50)
    assert result["truncated"] is False
    assert result["scanned_collections"] == len(tree)
    assert result["locations"] == [
        {"path": "Apps/Books/.Moon+/Cache", "position_files": 1, "last_modified": None},
        {"path": "Moon/.Moon+/Cache", "position_files": 1,
         "last_modified": modified.isoformat()},
    ]


def test_book_matcher_uses_exact_filename_and_unique_stem():
    from cps.services.moonreader_webdav import BookMatch, BookMatcher
    matcher = BookMatcher.__new__(BookMatcher)
    matcher._exact = {}
    matcher._stem = {}
    matcher._local_files = []
    matcher._add("Console Wars - Blake Harris.fb2", BookMatch(146, "FB2", "x", "seed"))
    exact = matcher.match_filename(".Moon+/Cache/Console Wars - Blake Harris.fb2.po")
    stem = matcher.match_filename(".Moon+/Cache/Console Wars - Blake Harris.epub.po")
    assert exact.book_id == 146 and exact.method == "filename"
    assert stem.book_id == 146 and stem.method == "filename_stem"


def test_book_matcher_uses_calibre_export_book_id_with_same_format():
    from cps.services.moonreader_webdav import BookMatch, BookMatcher
    matcher = BookMatcher.__new__(BookMatcher)
    matcher._by_id_format = {
        (2, "EPUB"): BookMatch(2, "EPUB", "V ughonie.epub", "seed"),
    }
    result = matcher.match_calibre_export_id(
        ".Moon+/Cache/V ughonie - Devid Kushnier (2).epub.po")
    assert result.book_id == 2
    assert result.format == "EPUB"
    assert result.method == "calibre_id"
    assert matcher.match_calibre_export_id(
        ".Moon+/Cache/Olympia Progress (2).epub.po") is None
    assert matcher.match_calibre_export_id(
        ".Moon+/Cache/V ughonie - Devid Kushnier (2).pdf.po") is None


def test_checksum_fallback_matches_identical_remote_copy(tmp_path):
    from cps.services.moonreader_webdav import BookMatch, BookMatcher
    payload = b"same book bytes"
    local = tmp_path / "book.fb2"
    local.write_bytes(payload)
    matcher = BookMatcher.__new__(BookMatcher)
    matcher._exact = {}
    matcher._stem = {}
    matcher._local_files = [(BookMatch(146, "FB2", local.name, "seed"), str(local), len(payload))]
    import hashlib
    result = matcher.match_checksum(hashlib.sha256(payload).hexdigest(), len(payload))
    assert result.book_id == 146
    assert result.method == "sha256"


def test_apply_position_stores_raw_locator_and_normalized_progress():
    from cps.services import moonreader_webdav as mod
    from cps.progress_syncing.models import KOSyncProgress
    resource = mod.WebDavResource(".Moon+/Cache/Book.fb2.po", False, etag="e")
    position = mod.parse_position("1703297605115*4@0#99:42.5%")
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3)
    session = MagicMock()

    def query(model):
        q = MagicMock()
        if model is mod.ub.MoonReaderProgress:
            q.filter.return_value.first.return_value = None
        elif model is KOSyncProgress:
            q.filter.return_value.order_by.return_value.first.return_value = None
        return q

    session.query.side_effect = query
    with patch.object(mod.ub, "session", session), \
         patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=False), \
         patch("cps.progress_syncing.protocols.kosync.get_book_checksums", return_value=[]), \
         patch("cps.progress_syncing.protocols.kosync.update_book_read_status") as update:
        result = mod._apply_position(user, resource, position, match)

    assert result == "updated"
    added = [call.args[0] for call in session.add.call_args_list]
    moon = next(row for row in added if isinstance(row, mod.ub.MoonReaderProgress))
    kosync = next(row for row in added if isinstance(row, KOSyncProgress))
    assert moon.raw_position == position.raw
    assert moon.percentage == 42.5
    assert moon.chapter == 4
    assert kosync.document == "7"
    assert kosync.progress == position.raw
    assert kosync.device == "Moon+ Reader"
    update.assert_called_once_with(user, 7, 42.5)
    session.commit.assert_called_once()


def test_newer_other_device_progress_is_not_overwritten():
    from cps.services import moonreader_webdav as mod
    from cps.progress_syncing.models import KOSyncProgress
    resource = mod.WebDavResource(".Moon+/Cache/Book.fb2.po", False)
    position = mod.parse_position("1703297605115*4@0#99:42.5%")
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3)
    newer = SimpleNamespace(
        timestamp=datetime(2026, 8, 3, tzinfo=timezone.utc),
        device_id="koreader-phone",
    )
    session = MagicMock()

    def query(model):
        q = MagicMock()
        if model is mod.ub.MoonReaderProgress:
            q.filter.return_value.first.return_value = None
        elif model is KOSyncProgress:
            q.filter.return_value.order_by.return_value.first.return_value = newer
        return q

    session.query.side_effect = query
    with patch.object(mod.ub, "session", session), \
         patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=False), \
         patch("cps.progress_syncing.protocols.kosync.get_book_checksums", return_value=["sha"]), \
         patch("cps.progress_syncing.protocols.kosync.update_book_read_status") as update:
        result = mod._apply_position(user, resource, position, match)

    assert result == "stored_only"
    update.assert_not_called()
    assert all(not isinstance(call.args[0], KOSyncProgress)
               for call in session.add.call_args_list)
    session.commit.assert_called_once()


def test_native_locator_uses_pdf_page_and_reflowable_cfi():
    from cps.services import moonreader_webdav as mod
    pdf = mod.parse_position("1703297605115*28:9.4%")
    epub = mod.parse_position("1703297605115*4@0#99:42.5%")
    assert mod._native_locator(pdf, "PDF") == (
        '{"type":"pdf-position","version":1,"page":28,"scroll":{"x":0,"y":0}}'
    )
    assert mod._native_locator(epub, "FB2") == "epubcfi(/6/2!/4/2)"


def test_manual_sync_backfills_missing_native_calibre_position():
    from cps.services import moonreader_webdav as mod
    resource = mod.WebDavResource(
        ".Moon+/Cache/Book.fb2.po", False,
        modified=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )
    position = mod.parse_position("1703297605115*4@0#99:42.5%")
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")

    with patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
         patch("cps.services.calibremcp_client.get_reader_position",
               return_value={"positions": []}), \
         patch("cps.services.calibremcp_client.set_reader_position") as save:
        changed = mod._sync_native_position(user, resource, position, match)

    assert changed is True
    save.assert_called_once_with(
        "admin", 7, "FB2", cfi="epubcfi(/6/2!/4/2)", position_fraction=.425,
        device="moonreader-webdav:3",
    )


def test_manual_sync_replaces_technical_native_zero():
    from cps.services import moonreader_webdav as mod
    resource = mod.WebDavResource(
        ".Moon+/Cache/Book.fb2.po", False,
        modified=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )
    position = mod.parse_position("1703297605115*4@0#99:42.5%")
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")
    native = {"positions": [{
        "pos_frac": 0.0,
        "epoch": datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp(),
    }]}

    with patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
         patch("cps.services.calibremcp_client.get_reader_position", return_value=native), \
         patch("cps.services.calibremcp_client.set_reader_position") as save:
        changed = mod._sync_native_position(user, resource, position, match)

    assert changed is True
    assert save.call_args.kwargs["position_fraction"] == .425


def test_manual_sync_keeps_newer_nonzero_native_position():
    from cps.services import moonreader_webdav as mod
    resource = mod.WebDavResource(
        ".Moon+/Cache/Book.fb2.po", False,
        modified=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )
    position = mod.parse_position("1703297605115*4@0#99:42.5%")
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")
    native = {"positions": [{
        "pos_frac": .67,
        "epoch": datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp(),
    }]}

    with patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
         patch("cps.services.calibremcp_client.get_reader_position", return_value=native), \
         patch("cps.services.calibremcp_client.set_reader_position") as save:
        changed = mod._sync_native_position(user, resource, position, match)

    assert changed is False
    save.assert_not_called()


def test_unchanged_moon_file_can_backfill_native_position():
    from cps.services import moonreader_webdav as mod
    from cps.progress_syncing.models import KOSyncProgress
    resource = mod.WebDavResource(".Moon+/Cache/Book.fb2.po", False)
    position = mod.parse_position("1703297605115*4@0#99:42.5%")
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")
    existing = SimpleNamespace(moon_timestamp=position.timestamp)
    own = SimpleNamespace(timestamp=position.timestamp, device_id="moonreader-webdav:3")
    session = MagicMock()

    def query(model):
        q = MagicMock()
        if model is mod.ub.MoonReaderProgress:
            q.filter.return_value.first.return_value = existing
        elif model is KOSyncProgress:
            q.filter.return_value.order_by.return_value.first.return_value = own
        return q

    session.query.side_effect = query
    with patch.object(mod.ub, "session", session), \
         patch("cps.progress_syncing.protocols.kosync.get_book_checksums", return_value=[]), \
         patch.object(mod, "_sync_native_position", return_value=True) as native:
        result = mod._apply_position(user, resource, position, match)

    assert result == "updated"
    native.assert_called_once_with(user, resource, position, match)
    session.commit.assert_not_called()
