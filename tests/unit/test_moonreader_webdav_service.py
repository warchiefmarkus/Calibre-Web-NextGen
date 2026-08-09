# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_parse_moonreader_position_uses_device_id_not_timestamp():
    from cps.services.moonreader_webdav import parse_position
    value = parse_position("1703297605115*21@0#4826:11.1%")
    assert value.device_id == "1703297605115"
    assert value.chapter == 21
    assert value.split_index == 0
    assert value.offset == 4826
    assert value.percentage == 11.1


@pytest.mark.parametrize(("raw", "chapter", "split_index", "offset", "percentage"), [
    ("1634339204311*34:15.0%", 34, None, 34, 15.0),
    ("1634339204311*0:100%", 0, None, 0, 100.0),
    ("1634339204311*0@0#0:0,00%", 0, 0, 0, 0.0),
])
def test_parse_moonreader_position_supports_pdf_and_decimal_comma(
        raw, chapter, split_index, offset, percentage):
    from cps.services.moonreader_webdav import parse_position
    value = parse_position(raw)
    assert value.chapter == chapter
    assert value.split_index == split_index
    assert value.offset == offset
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


def test_position_serializer_roundtrips_moon_format():
    from cps.services.moonreader_locator import parse_position, serialize_position
    raw = serialize_position(
        device_id="1234567890123", chapter=6, split_index=0,
        offset=2768, percentage=2.4468,
    )
    assert raw == "1234567890123*6@0#2768:2.4%"
    value = parse_position(raw)
    assert value.device_id == "1234567890123"
    assert value.chapter == 6
    assert value.offset == 2768


def test_fb2_mapping_uses_moon_chapter_and_character_offset(tmp_path):
    from cps.services.moonreader_locator import (
        fb2_chapters, fraction_from_locator, map_book_position,
    )
    path = tmp_path / "book.fb2"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        '<body><section><title><p>Intro</p></title><p>one two three</p></section>'
        '<section><title><p>Chapter</p></title><p>before target sentence after</p>'
        '</section></body></FictionBook>'
    )
    path.write_text(xml, encoding="utf-8")
    chapters = fb2_chapters(str(path))
    mapped = map_book_position(
        str(path), "FB2", .7, anchor_text="target sentence after",
    )
    assert mapped.chapter == 1
    assert mapped.split_index == 0
    assert chapters[1].text[mapped.offset:].startswith("target sentence")
    assert mapped.matched_anchor is True
    assert mapped.percentage == pytest.approx(
        fraction_from_locator(chapters, 1, mapped.offset) * 100,
    )


def test_console_wars_locator_model_matches_moon_percentage_when_fixture_available():
    from cps.services.moonreader_locator import (
        fb2_chapters, fraction_from_locator, map_book_position, parse_position,
    )
    path = Path(
        "/root/calibre/Library/Blieik Dzh Kharris/Konsol'nyie voiny (146)/"
        "Konsol'nyie voiny - Blieik Dzh Kharris.fb2"
    )
    if not path.exists():
        pytest.skip("deployment fixture is not available")
    chapters = fb2_chapters(str(path))
    assert chapters[6].text.find("Рад видеть тебя, Накаяма-сан") in range(2760, 2780)
    assert fraction_from_locator(chapters, 6, 2768) * 100 == pytest.approx(2.4468, abs=.01)
    mapped = map_book_position(
        str(path), "FB2", .024,
        anchor_text="Рад видеть тебя, Накаяма-сан",
        remote_position=parse_position("1634339204311*6@0#2768:2.4%"),
    )
    assert mapped.chapter == 6
    assert mapped.split_index == 0
    assert mapped.offset in range(2768, 2775)
    assert mapped.matched_anchor is True


def test_console_wars_moon_anchor_distinguishes_26_percent_from_foliate_24_percent():
    from cps.services.moonreader_locator import anchor_from_locator, fb2_chapters, parse_position
    path = Path(
        "/root/calibre/Library/Blieik Dzh Kharris/Konsol'nyie voiny (146)/"
        "Konsol'nyie voiny - Blieik Dzh Kharris.fb2"
    )
    if not path.exists():
        pytest.skip("deployment fixture is not available")
    chapters = fb2_chapters(str(path))
    moon = parse_position("1634339204311*6@0#4653:2.6%")
    anchor = anchor_from_locator(chapters, moon)
    assert anchor is not None
    assert anchor.startswith("И тут, прямо посреди семейного отпуска")
    assert not anchor.startswith("врасплох, и поэтому любой дискомфорт")


def test_webdav_conditional_put_uses_etag_and_reports_race():
    from cps.services.moonreader_webdav import MoonReaderError, WebDavClient
    client = WebDavClient("http://host/books/", "reader", "secret")
    ok = MagicMock(status_code=204)
    with patch.object(client.session, "request", return_value=ok) as request:
        client.put_bytes("Moon/.Moon+/Cache/Book.fb2.po", b"x", etag='"abc"')
    assert request.call_args.kwargs["headers"]["If-Match"] == '"abc"'

    race = MagicMock(status_code=412)
    with patch.object(client.session, "request", return_value=race), \
         pytest.raises(MoonReaderError) as exc:
        client.put_bytes("Moon/.Moon+/Cache/Book.fb2.po", b"x", etag='"abc"')
    assert exc.value.code == "write_conflict"


def _native(fraction, when, device="cwng-web-test"):
    return {"pos_frac": fraction, "epoch": when.timestamp(), "device": device}


def test_conflict_policy_moon_wins_ties_and_technical_native_zero():
    from cps.services import moonreader_webdav as mod
    when = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)
    resource = mod.WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False, modified=when)
    position = mod.parse_position("1111111111111*6@0#2768:2.4%")
    assert mod._conflict_direction(resource, position, _native(.8, when), "222") == "from_moon"
    assert mod._conflict_direction(
        resource, position, _native(0, when.replace(hour=11)), "222",
    ) == "from_moon"


def test_conflict_policy_newer_calibre_position_writes_to_moon():
    from cps.services import moonreader_webdav as mod
    remote = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)
    native = datetime(2026, 8, 4, 10, 5, tzinfo=timezone.utc)
    resource = mod.WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False, modified=remote)
    position = mod.parse_position("1111111111111*6@0#2768:2.4%")
    assert mod._conflict_direction(resource, position, _native(.3, native), "222") == "to_moon"


def test_unseen_moon_zero_does_not_erase_nonzero_native_position():
    from cps.services import moonreader_webdav as mod
    native_time = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)
    remote_time = datetime(2026, 8, 4, 11, tzinfo=timezone.utc)
    resource = mod.WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False, modified=remote_time)
    position = mod.parse_position("1111111111111*0@0#0:0.0%")

    assert mod._conflict_direction(
        resource, position, _native(.42, native_time), "2222222222222", None,
    ) == "to_moon"


def test_zero_over_server_seed_is_bootstrap_not_authoritative_reset():
    from cps.services import moonreader_webdav as mod
    native_time = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)
    remote_time = datetime(2026, 8, 4, 11, tzinfo=timezone.utc)
    resource = mod.WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False, modified=remote_time)
    position = mod.parse_position("1111111111111*0@0#0:0.0%")
    tracking = SimpleNamespace(
        percentage=42.0, remote_device_id="2222222222222", last_direction="unchanged",
    )

    assert mod._conflict_direction(
        resource, position, _native(.42, native_time), "2222222222222", tracking,
    ) == "to_moon"


def test_legacy_unknown_tracking_zero_does_not_erase_nonzero_native():
    from cps.services import moonreader_webdav as mod
    native_time = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)
    remote_time = datetime(2026, 8, 4, 11, tzinfo=timezone.utc)
    resource = mod.WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False, modified=remote_time)
    position = mod.parse_position("1111111111111*0@0#0:0.0%")
    tracking = SimpleNamespace(
        percentage=42.0, remote_device_id=None, last_direction=None,
    )

    assert mod._conflict_direction(
        resource, position, _native(.42, native_time), "2222222222222", tracking,
    ) == "to_moon"


def test_known_moon_origin_can_intentionally_reset_to_zero():
    from cps.services import moonreader_webdav as mod
    native_time = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)
    remote_time = datetime(2026, 8, 4, 11, tzinfo=timezone.utc)
    resource = mod.WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False, modified=remote_time)
    position = mod.parse_position("1111111111111*0@0#0:0.0%")
    tracking = SimpleNamespace(
        percentage=42.0, remote_device_id="1111111111111", last_direction="from_moon",
    )

    assert mod._conflict_direction(
        resource, position, _native(.42, native_time), "2222222222222", tracking,
    ) == "from_moon"


def test_conflict_policy_suppresses_server_echo():
    from cps.services import moonreader_webdav as mod
    remote = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)
    resource = mod.WebDavResource("Moon/.Moon+/Cache/Book.fb2.po", False, modified=remote)
    position = mod.parse_position("2222222222222*6@0#2768:2.4%")
    native = _native(.024, remote.replace(minute=1), "cwng-web-test")
    assert mod._conflict_direction(resource, position, native, "2222222222222") == "unchanged"


def test_native_pairs_use_calibre_reader_namespace(tmp_path, monkeypatch):
    import sqlite3
    from cps.services import moonreader_webdav as mod

    db_path = tmp_path / "metadata.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE last_read_positions (user TEXT, book INTEGER, format TEXT)"
        )
        connection.execute(
            "INSERT INTO last_read_positions(user, book, format) VALUES (?, ?, ?)",
            ("cwng-admin", 7, "fb2"),
        )
        connection.execute(
            "INSERT INTO last_read_positions(user, book, format) VALUES (?, ?, ?)",
            ("somebody-else", 8, "epub"),
        )
    monkeypatch.delenv("CWNG_NATIVE_READER_USERNAME", raising=False)
    monkeypatch.delenv("CWNG_NATIVE_READER_USERNAME_TEMPLATE", raising=False)
    monkeypatch.setattr(mod.config, "config_calibre_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(
        mod.deployment_profile, "use_calibre_native_reader_data", lambda: True,
    )

    assert mod._native_pairs("admin") == {(7, "FB2")}


def test_remote_path_ignores_tracking_from_an_obsolete_cache_folder():
    from cps.services import moonreader_webdav as mod

    match = mod.BookMatch(7, "FB2", "Book.fb2", "book_id")
    legacy = SimpleNamespace(remote_path=".Moon+/Cache/Old-name.fb2.po")

    assert mod._remote_path("Moon/.Moon+/Cache", match, legacy) == (
        "Moon/.Moon+/Cache/Book.fb2.po"
    )


def test_remote_path_reuses_tracking_inside_selected_cache_folder():
    from cps.services import moonreader_webdav as mod

    match = mod.BookMatch(7, "FB2", "Book.fb2", "book_id")
    current = SimpleNamespace(
        remote_path="Moon/.Moon+/Cache/Moon-native-name.fb2.po",
    )

    assert mod._remote_path("Moon/.Moon+/Cache", match, current) == (
        "Moon/.Moon+/Cache/Moon-native-name.fb2.po"
    )


def test_missing_remote_position_is_created_from_native_calibre_state():
    from cps.services import moonreader_webdav as mod

    client = MagicMock()
    matcher = MagicMock()
    match = mod.BookMatch(7, "FB2", "Book.fb2", "book_id")
    user = SimpleNamespace(id=3, name="admin")
    native = {"positions": [{
        "pos_frac": .42,
        "epoch": datetime(2026, 8, 4, 11, tzinfo=timezone.utc).timestamp(),
        "device": "cwng-web-live",
        "cfi": "epubcfi(/6/4!/4/2)",
    }]}
    with patch.object(
        mod.deployment_profile, "use_calibre_native_reader_data", return_value=True,
    ), patch.object(mod, "moon_device_id", return_value="2222222222222"), patch(
        "cps.services.calibremcp_client.get_reader_position", return_value=native,
    ), patch.object(mod, "_export_native", return_value="uploaded") as export:
        result = mod.reconcile_book(
            user, client, "Moon/.Moon+/Cache", matcher, match, None,
        )

    assert result == "uploaded"
    export.assert_called_once()
    assert export.call_args.args[3] is None


def test_native_locator_uses_pdf_page_and_self_describing_moon_carrier():
    from cps.services import moonreader_webdav as mod
    pdf = mod.parse_position("1703297605115*28:9.4%")
    epub = mod.parse_position("1703297605115*4@0#99:42.5%")
    assert mod._native_locator(pdf, "PDF") == (
        '{"type":"pdf-position","version":1,"page":28,"scroll":{"x":0,"y":0}}'
    )
    assert mod._native_locator(epub, "FB2") == (
        "moonreader-webdav:1703297605115*4@0#99:42.5%"
    )


def test_export_existing_duplicate_writes_the_resource_being_reconciled():
    from cps.services import moonreader_webdav as mod

    resource = mod.WebDavResource(
        "Moon/.Moon+/Cache/target-name.fb2.po", False,
        etag='"target-etag"', modified=datetime.now(timezone.utc),
    )
    other_tracking = SimpleNamespace(
        remote_path="Moon/.Moon+/Cache/other-name.fb2.po",
    )
    query = MagicMock()
    query.filter.return_value.first.return_value = other_tracking
    session = MagicMock()
    session.query.return_value = query
    client = MagicMock()
    client.resource_in_collection.return_value = resource
    matcher = MagicMock()
    matcher.local_path.return_value = None
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")
    native = {
        "pos_frac": .42,
        "epoch": datetime(2026, 8, 4, 11, tzinfo=timezone.utc).timestamp(),
    }
    mapped = SimpleNamespace(
        chapter=4, split_index=0, offset=123, percentage=42.0,
        matched_anchor=False,
    )

    with patch.object(mod.ub, "session", session),          patch.object(mod, "moon_device_id", return_value="2222222222222"),          patch.object(mod, "map_book_position", return_value=mapped),          patch.object(mod, "_record_progress"):
        result = mod._export_native(
            user, client, "Moon/.Moon+/Cache", resource, match, matcher, native,
        )

    assert result == "uploaded"
    args, kwargs = client.put_bytes.call_args
    assert args[0] == resource.path
    assert kwargs["etag"] == '"target-etag"'
    assert kwargs["create_only"] is False


def test_first_open_zero_is_repaired_from_cwng_web_without_anchor():
    from cps.services import moonreader_webdav as mod

    resource = mod.WebDavResource(
        "Moon/.Moon+/Cache/Book.fb2.po", False, etag='"zero"',
        modified=datetime(2026, 8, 7, 20, 7, tzinfo=timezone.utc),
    )
    client = MagicMock()
    client.get_bytes.return_value = b"1111111111111*0@0#0:0.0%"
    matcher = MagicMock()
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")
    native = {"positions": [{
        "pos_frac": .42,
        "epoch": datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc).timestamp(),
        "device": "cwng-web-existing",
        "cfi": "epubcfi(/6/4!/4/2)",
    }]}
    query = MagicMock()
    query.filter.return_value.first.return_value = None
    session = MagicMock()
    session.query.return_value = query
    with patch.object(mod.ub, "session", session),          patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True),          patch.object(mod, "moon_device_id", return_value="2222222222222"),          patch("cps.services.calibremcp_client.get_reader_position", return_value=native),          patch.object(mod, "_export_native", return_value="uploaded") as export:
        result = mod.reconcile_book(
            user, client, "Moon/.Moon+/Cache", matcher, match, resource,
            anchor_text=None,
        )

    assert result == "uploaded"
    export.assert_called_once()
    assert export.call_args.kwargs["remote_position"].percentage == 0.0


def test_existing_moon_file_is_not_overwritten_by_old_web_position_without_anchor():
    from cps.services import moonreader_webdav as mod
    resource = mod.WebDavResource(
        "Moon/.Moon+/Cache/Book.fb2.po", False, etag='"remote"',
        modified=datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
    )
    client = MagicMock()
    client.get_bytes.return_value = b"1111111111111*6@0#2768:2.4%"
    matcher = MagicMock()
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")
    native = {"positions": [{
        "pos_frac": .5,
        "epoch": datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc).timestamp(),
        "device": "cwng-web-old",
        "cfi": "epubcfi(/6/4!/4/2)",
    }]}
    query = MagicMock()
    query.filter.return_value.first.return_value = None
    session = MagicMock()
    session.query.return_value = query
    with patch.object(mod.ub, "session", session), \
         patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
         patch.object(mod, "moon_device_id", return_value="2222222222222"), \
         patch("cps.services.calibremcp_client.get_reader_position", return_value=native), \
         patch.object(mod, "_export_native") as export:
        result = mod.reconcile_book(
            user, client, "Moon/.Moon+/Cache", matcher, match, resource,
            anchor_text=None,
        )
    assert result == "deferred"
    export.assert_not_called()


def test_server_written_position_with_same_etag_does_not_echo_back():
    from cps.services import moonreader_webdav as mod
    resource = mod.WebDavResource(
        "Moon/.Moon+/Cache/Book.fb2.po", False, etag='"same"',
        modified=datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
    )
    client = MagicMock()
    client.get_bytes.return_value = b"2222222222222*6@0#2771:2.4%"
    matcher = MagicMock()
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    user = SimpleNamespace(id=3, name="admin")
    native_epoch = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc).timestamp()
    native = {"positions": [{
        "pos_frac": .024468,
        "epoch": native_epoch,
        "device": "cwng-web-live",
        "cfi": "epubcfi(/6/4!/4/2)",
    }]}
    tracking = SimpleNamespace(
        last_direction="to_moon", remote_etag='"same"',
        last_native_epoch=native_epoch,
    )
    query = MagicMock()
    query.filter.return_value.first.return_value = tracking
    session = MagicMock()
    session.query.return_value = query
    with patch.object(mod.ub, "session", session), \
         patch.object(mod, "moon_device_id", return_value="2222222222222"), \
         patch("cps.services.calibremcp_client.get_reader_position", return_value=native), \
         patch.object(mod, "_record_progress") as record, \
         patch.object(mod, "_import_remote") as download, \
         patch.object(mod, "_export_native") as upload:
        result = mod.reconcile_book(
            user, client, "Moon/.Moon+/Cache", matcher, match, resource,
        )
    assert result == "unchanged"
    record.assert_called_once()
    download.assert_not_called()
    upload.assert_not_called()


def test_unsupported_mobi_writeback_is_deferred_instead_of_writing_zero_locator():
    from cps.services import moonreader_webdav as mod
    client = MagicMock()
    matcher = MagicMock()
    match = mod.BookMatch(74, "MOBI", "Book.mobi", "book_id")
    user = SimpleNamespace(id=3, name="admin")
    native = {"positions": [{
        "pos_frac": .25,
        "epoch": datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc).timestamp(),
        "device": "external-reader",
        "cfi": "some-mobi-locator",
    }]}
    session = MagicMock()
    with patch.object(mod.ub, "session", session), \
         patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
         patch.object(mod, "moon_device_id", return_value="2222222222222"), \
         patch("cps.services.calibremcp_client.get_reader_position", return_value=native), \
         patch.object(mod, "_export_native") as export:
        result = mod.reconcile_book(
            user, client, "Moon/.Moon+/Cache", matcher, match, None,
        )
    assert result == "deferred"
    export.assert_not_called()


def test_named_fb2_main_body_is_supported(tmp_path):
    from cps.services.moonreader_locator import fb2_chapters

    path = tmp_path / "named-body.fb2"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        '<body name="Main"><section><title><p>Chapter</p></title>'
        '<p>Readable text</p></section></body></FictionBook>',
        encoding="utf-8",
    )

    chapters = fb2_chapters(str(path))
    assert len(chapters) == 1
    assert chapters[0].text == "Chapter Readable text"


def test_sync_reconciles_all_remote_files_matching_same_book_oldest_first():
    from cps.services import moonreader_webdav as mod

    older = mod.WebDavResource(
        "Moon/.Moon+/Cache/old-name.fb2.po", False,
        modified=datetime(2026, 8, 5, 10, tzinfo=timezone.utc),
    )
    newer = mod.WebDavResource(
        "Moon/.Moon+/Cache/new-name.fb2.po", False,
        modified=datetime(2026, 8, 7, 10, tzinfo=timezone.utc),
    )
    settings = SimpleNamespace(
        enabled=True, cache_path="Moon/.Moon+/Cache",
        base_url="http://host/books/", username="reader",
        password_encrypted="encrypted",
    )
    user = SimpleNamespace(id=1, name="admin")
    match = mod.BookMatch(7, "FB2", "Book.fb2", "filename")
    client = MagicMock()
    client.discover_positions.return_value = (
        "Moon/.Moon+/Cache", [newer, older], True,
    )
    client.root_files.return_value = []
    matcher = MagicMock()
    session = MagicMock()
    session.get.return_value = user
    order = []

    def reconcile(_user, _client, _cache, _matcher, _match, resource, **_kwargs):
        order.append(resource.path)
        return "unchanged"

    with patch.object(mod, "get_or_create_settings", return_value=settings),          patch.object(mod, "decrypt_password", return_value="secret"),          patch.object(mod, "WebDavClient", return_value=client),          patch.object(mod, "BookMatcher", return_value=matcher),          patch.object(mod, "_match_remote", return_value=match),          patch.object(mod, "reconcile_book", side_effect=reconcile),          patch.object(mod.ub, "session", session):
        summary = mod.sync_positions(1, include_native_only=False)

    assert order == [older.path, newer.path]
    assert summary["matched"] == 2
    assert summary["unchanged"] == 2
    client.close.assert_called_once()


def test_long_html_anchor_maps_to_moon_split_and_infers_device_profile():
    from cps.services.moonreader_locator import (
        MoonChapter, infer_split_size, map_fraction_to_moon, normalize_text,
        parse_position, serialize_position,
    )

    paragraphs = []
    for index in range(24):
        prefix = "TARGET-SENTENCE " if index == 10 else f"paragraph-{index} "
        paragraphs.append(f"<p>{prefix}{'x' * 19_980}</p>")
    source = "".join(paragraphs)
    chapter = MoonChapter(index=0, text=normalize_text(source), source_html=source)

    mapped = map_fraction_to_moon(
        [chapter], .45, anchor_text="TARGET-SENTENCE", split_size=150_000,
    )
    assert mapped.chapter == 0
    assert mapped.split_index == 1
    assert mapped.matched_anchor is True

    remote = parse_position(serialize_position(
        device_id="1234567890123", chapter=mapped.chapter,
        split_index=mapped.split_index, offset=mapped.offset,
        percentage=mapped.percentage,
    ))
    assert infer_split_size([chapter], remote) == 150_000


def test_moon_percentage_is_fallback_when_local_locator_cannot_be_verified():
    from cps.services import moonreader_webdav as mod

    position = mod.parse_position("1234567890123*8@2#99999:12.3%")
    matcher = MagicMock()
    matcher.local_path.return_value = None
    assert mod._remote_fraction(position, MagicMock(), matcher) == pytest.approx(.123)


def test_verified_moon_locator_preserves_sub_decimal_precision(tmp_path):
    from cps.services import moonreader_webdav as mod

    path = tmp_path / "book.fb2"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        '<body><section><title><p>One</p></title><p>alpha beta gamma</p>'
        '</section><section><title><p>Two</p></title><p>delta epsilon zeta'
        '</p></section></body></FictionBook>',
        encoding="utf-8",
    )
    chapters = mod.chapters_for_book(str(path), "FB2")
    exact = mod.fraction_from_locator(chapters, 1, 5)
    position = mod.parse_position(mod.serialize_moon_position(
        device_id="1234567890123", chapter=1, split_index=0, offset=5,
        percentage=exact * 100.0,
    ))
    matcher = MagicMock()
    matcher.local_path.return_value = str(path)
    match = mod.BookMatch(1, "FB2", path.name, "test")
    assert mod._remote_fraction(position, match, matcher) == pytest.approx(exact)


def test_book_scoped_sync_without_format_reconciles_all_native_formats():
    from cps.services import moonreader_webdav as mod

    settings = SimpleNamespace(
        enabled=True, base_url="http://host/books/", username="reader",
        password_encrypted="encrypted", cache_path="Moon/.Moon+/Cache",
    )
    user = SimpleNamespace(id=3, name="admin")
    client = MagicMock()
    client.discover_positions.return_value = ("Moon/.Moon+/Cache", [], True)
    client.root_files.return_value = []
    matcher = MagicMock()
    matcher.for_book.side_effect = lambda book_id, fmt=None: mod.BookMatch(
        int(book_id), str(fmt).upper() if fmt else "FB2", f"Book.{str(fmt or 'fb2').lower()}", "book_id",
    )
    session = MagicMock()
    session.get.return_value = user
    with patch.object(mod, "get_or_create_settings", return_value=settings), \
         patch.object(mod, "decrypt_password", return_value="secret"), \
         patch.object(mod, "WebDavClient", return_value=client), \
         patch.object(mod, "BookMatcher", return_value=matcher), \
         patch.object(mod.ub, "session", session), \
         patch.object(mod, "_native_pairs", return_value={(7, "FB2"), (7, "EPUB"), (8, "PDF")}), \
         patch.object(mod, "reconcile_book", return_value="unchanged") as reconcile:
        summary = mod.sync_positions(3, book_id=7, fmt=None, include_native_only=True)

    assert summary["unchanged"] == 2
    assert {(call.args[4].book_id, call.args[4].format) for call in reconcile.call_args_list} == {
        (7, "FB2"), (7, "EPUB"),
    }
    client.close.assert_called_once()
