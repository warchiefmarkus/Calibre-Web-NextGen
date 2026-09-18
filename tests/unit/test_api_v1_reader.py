# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 reader bookmark endpoints (auth gate + format casing +
the save/clear write path). GET contracts use SQLite; write/settings tests use
mocks. Legacy interop uses the same row and lowercase format."""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="GET", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _auth_user(*, browse_global=False):
    return SimpleNamespace(
        is_authenticated=True, is_anonymous=False, id=1,
        role_browse_global=lambda: browse_global,
    )


def _visible_book(mod, *, visible=True):
    book = SimpleNamespace(id=5) if visible else None
    return patch.object(mod.calibre_db, "get_filtered_book", return_value=book)


@pytest.mark.unit
def test_get_bookmark_anonymous_401():
    from cps.api import reader as mod
    with _ctx("/api/v1/books/5/bookmark?format=epub"):
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=False, is_anonymous=True)):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert resp[1] == 401


@pytest.fixture
def bookmark_client(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub
    from cps.api import reader as mod
    # The app session supports in-memory SQLite too; optional carrier failure
    # must not change the GET contract of the mandatory local store.
    engine = create_engine('sqlite:///:memory:')
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, 'session', session)
    monkeypatch.setattr(mod, 'current_user', _auth_user())
    monkeypatch.setattr(mod, '_require_visible_book', lambda _book_id: None)
    app = flask.Flask(__name__)
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark',
                     view_func=inspect.unwrap(mod.get_bookmark))
    yield app.test_client(), session
    session.close()
    engine.dispose()


def test_get_bookmark_returns_key(bookmark_client):
    from cps import ub
    client, session = bookmark_client
    session.add(ub.Bookmark(user_id=1, book_id=5, format='epub',
                           bookmark_key='epubcfi(/6/8)'))
    session.commit()
    response = client.get('/api/v1/books/5/bookmark?format=EPUB')
    assert response.status_code == 200
    assert response.json == {'bookmark': 'epubcfi(/6/8)', 'resume': None}


def test_get_bookmark_none_when_absent(bookmark_client):
    from cps import ub
    client, session = bookmark_client
    session.add_all([
        ub.Bookmark(user_id=2, book_id=5, format='epub', bookmark_key='other-user'),
        ub.Bookmark(user_id=1, book_id=6, format='epub', bookmark_key='other-book'),
        ub.Bookmark(user_id=1, book_id=5, format='pdf', bookmark_key='other-format'),
    ])
    session.commit()
    response = client.get('/api/v1/books/5/bookmark')
    assert response.status_code == 200
    assert response.json == {'bookmark': None, 'resume': None}


@pytest.mark.unit
def test_get_bookmark_returns_404_for_invisible_book():
    from cps.api import reader as mod
    with _ctx("/api/v1/books/5/bookmark"):
        with patch.object(mod, "current_user", _auth_user()), _visible_book(mod, visible=False):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert resp[1] == 404


@pytest.mark.unit
def test_native_bookmark_uses_moon_fraction_over_newer_zero_initialization():
    from cps.api import reader as mod
    native_payload = {"positions": [{
        "cfi": "epubcfi(/6/2!/4/2)", "pos_frac": 0.0, "epoch": 1785764310.0,
    }]}
    with _ctx("/api/v1/books/5/bookmark?format=fb2"):
        with patch.object(mod, "current_user", _auth_user()), _visible_book(mod), \
             patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
             patch.object(mod, "get_reader_position", return_value=native_payload), \
             patch.object(mod, "reading_progress_summary_map", return_value={5: {
                 "percentage": 28.5, "source": "moonreader",
                 "updated_at": "2026-04-10T23:31:28+00:00",
             }}):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    body = json.loads(resp.get_data())
    assert body == {
        "bookmark": None,
        "resume": None,
        "position_fraction": 0.285,
        "position_source": "moonreader",
        "position_anchor": None,
        "position_chapter": None,
        "position_section": None,
        "position_percentage": 28.5,
    }


@pytest.mark.unit
def test_native_moon_position_exposes_text_anchor_instead_of_fake_cfi():
    from cps.api import reader as mod
    user = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1, name="admin")
    native_payload = {"positions": [{
        "cfi": "epubcfi(/6/2!/4/2)", "pos_frac": .026026,
        "epoch": 1786313589.0, "device": "moonreader-webdav:1",
    }]}
    anchor = {"text": "И тут, прямо посреди семейного отпуска Калински",
              "chapter": 7, "foliate_section": 6, "percentage": 2.6}
    with _ctx("/api/v1/books/5/bookmark?format=fb2"):
        with patch.object(mod, "current_user", user), _visible_book(mod), \
             patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
             patch.object(mod, "get_reader_position", return_value=native_payload), \
             patch.object(mod, "reading_progress_summary_map", return_value={}), \
             patch("cps.services.moonreader_webdav.moon_position_anchor", return_value=anchor):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    body = json.loads(resp.get_data())
    assert body["bookmark"] is None
    assert body["position_source"] == "moonreader"
    assert body["position_anchor"].startswith("И тут, прямо посреди")
    assert body["position_chapter"] == 7
    assert body["position_section"] == 6
    assert body["position_percentage"] == 2.6
    assert body["position_fraction"] == pytest.approx(.026026)



@pytest.mark.unit
def test_save_bookmark_lowercases_format_and_merges():
    """Format must be stored lowercase (legacy interop) and the new bookmark merged."""
    from cps.api import reader as mod
    mock_ub = MagicMock()
    with _ctx("/api/v1/books/5/bookmark", method="POST",
              body={"format": "EPUB", "bookmark": "epubcfi(/6/8)"}):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod, "ub", mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.save_bookmark)(5)
    assert resp[1] == 204
    _args, kwargs = mock_ub.Bookmark.call_args
    assert kwargs["format"] == "epub", "format must be lowercased for legacy interop"
    assert kwargs["bookmark_key"] == "epubcfi(/6/8)"
    assert mock_ub.session.merge.called


@pytest.mark.unit
def test_save_empty_bookmark_clears_without_merge():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    with _ctx("/api/v1/books/5/bookmark", method="POST", body={"format": "epub", "bookmark": ""}):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod, "ub", mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.save_bookmark)(5)
    assert resp[1] == 204
    assert mock_ub.session.query.return_value.filter.return_value.delete.called
    assert not mock_ub.session.merge.called


@pytest.mark.unit
def test_source_preview_save_keeps_device_sharing_disabled():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    mock_ub.session_flush.return_value = True
    mock_ub.session_commit.return_value = True
    with _ctx("/api/v1/books/5/bookmark", method="POST", body={
        "format": "epub",
        "bookmark": "epubcfi(/6/8)",
        "percentage": 22.0,
        "share_with_devices": False,
    }):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod, "ub", mock_ub), _visible_book(mod), \
             patch.object(mod.reading_position, "coerce_percentage", return_value=22.0), \
             patch.object(mod.reading_position, "record_web_reader_progress") as record:
            resp = inspect.unwrap(mod.save_bookmark)(5)

    assert resp[1] == 204
    assert record.call_args.kwargs["share_with_devices"] is False


@pytest.mark.unit
def test_get_reader_settings_returns_complete_defaults_plus_saved_values():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {"reader": {
        "font": "Arial", "margin": 32,
        "translationEnabled": True, "translationView": "translated",
    }}
    with _ctx("/api/v1/reader/settings"):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.get_reader_settings)()
    body = json.loads(resp.get_data())["reader"]
    assert body["font"] == "Arial"
    assert body["margin"] == 32
    assert body["lineHeight"] == 150
    assert body["theme"] == "lightTheme"
    assert body["translationEnabled"] is False
    assert body["translationView"] == "original"


@pytest.mark.unit
def test_save_reader_settings_merges_partial_patch_without_erasing_siblings():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {"reader": {"font": "Arial", "margin": 32, "fontSize": 120}}
    mock_ub = MagicMock()
    with _ctx("/api/v1/reader/settings", method="POST", body={"lineHeight": 180}):
        with patch.object(mod, "current_user", user), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "flag_modified"):
            resp = inspect.unwrap(mod.save_reader_settings)()
    assert resp.status_code == 200
    assert user.view_settings["reader"] == {
        "font": "Arial", "margin": 32, "fontSize": 120, "lineHeight": 180,
    }
    assert json.loads(resp.get_data())["reader"]["lineHeight"] == 180
    mock_ub.session.commit.assert_called_once()


@pytest.mark.unit
def test_save_reader_settings_rejects_non_object_payload():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {}
    with _ctx("/api/v1/reader/settings", method="POST", body=["bad"]):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.save_reader_settings)()
    assert resp[1] == 400

@pytest.mark.unit
def test_get_reader_book_state_defaults_translation_off_for_unseen_book():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx('/api/v1/books/5/reader-state?format=EPUB'):
        with patch.object(mod, 'current_user', _auth_user()), patch.object(mod, 'ub', mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.get_reader_book_state)(5)
    assert json.loads(resp.get_data()) == {
        'book_id': 5, 'format': 'epub',
        'translationEnabled': False, 'translationView': 'original',
    }


@pytest.mark.unit
def test_save_reader_book_state_persists_translation_only_for_book_format():
    from cps.api import reader as mod
    row = SimpleNamespace(translation_enabled=False, translation_view='original')
    mock_ub = MagicMock()
    mock_ub.ReaderBookState.return_value = row
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx('/api/v1/books/5/reader-state', method='POST', body={
        'format': 'EPUB', 'translationEnabled': True, 'translationView': 'translated',
    }):
        with patch.object(mod, 'current_user', _auth_user()), patch.object(mod, 'ub', mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.save_reader_book_state)(5)
    assert json.loads(resp.get_data())['translationEnabled'] is True
    assert json.loads(resp.get_data())['translationView'] == 'translated'
    kwargs = mock_ub.ReaderBookState.call_args.kwargs
    assert kwargs['user_id'] == 1 and kwargs['book_id'] == 5 and kwargs['format'] == 'epub'
    mock_ub.session.add.assert_called_once_with(row)
    mock_ub.session.commit.assert_called_once()


@pytest.mark.unit
def test_save_reader_book_state_rejects_invalid_state_before_creating_row():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    with _ctx('/api/v1/books/5/reader-state', method='POST', body={
        'format': 'epub', 'translationView': 'everywhere',
    }):
        with patch.object(mod, 'current_user', _auth_user()), patch.object(mod, 'ub', mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.save_reader_book_state)(5)
    assert resp[1] == 400
    assert not mock_ub.session.query.called
    assert not mock_ub.session.add.called


@pytest.mark.unit
def test_global_reader_settings_ignore_book_scoped_translation_fields():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {'reader': {'font': 'Arial'}}
    mock_ub = MagicMock()
    with _ctx('/api/v1/reader/settings', method='POST', body={
        'lineHeight': 180, 'translationEnabled': True, 'translationView': 'translated',
    }):
        with patch.object(mod, 'current_user', user), patch.object(mod, 'ub', mock_ub), patch.object(mod, 'flag_modified'):
            resp = inspect.unwrap(mod.save_reader_settings)()
    assert resp.status_code == 200
    assert user.view_settings['reader']['lineHeight'] == 180
    assert 'translationEnabled' not in user.view_settings['reader']
    assert 'translationView' not in user.view_settings['reader']


@pytest.mark.unit
def test_reader_book_state_schema_is_scoped_by_user_book_and_format():
    from cps import ub
    table = ub.ReaderBookState.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == 'UniqueConstraint'
    }
    assert ('user_id', 'book_id', 'format') in unique_columns
    assert table.c.translation_enabled.default.arg is False
    assert table.c.translation_view.default.arg == 'original'


@pytest.mark.unit
def test_reader_bookmarks_require_visible_book():
    from cps.api import reader as mod
    with _ctx('/api/v1/books/5/reader-bookmarks'):
        with patch.object(mod, 'current_user', _auth_user()), _visible_book(mod, visible=False):
            resp = inspect.unwrap(mod.get_reader_bookmarks)(5)
    assert resp[1] == 404


@pytest.mark.unit
def test_reader_bookmarks_list_is_format_scoped():
    from cps.api import reader as mod
    row = SimpleNamespace(
        bookmark_id='cwn-reader-1', book_id=5, format='fb2', locator='foliate-locator',
        progression=.42, label='42', chapter='Chapter', created_at=None, updated_at=None,
    )
    mock_ub = MagicMock()
    query = mock_ub.session.query.return_value.filter.return_value
    query.filter.return_value.order_by.return_value.all.return_value = [row]
    with _ctx('/api/v1/books/5/reader-bookmarks?format=fb2'):
        with patch.object(mod, 'current_user', _auth_user()), patch.object(mod, 'ub', mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.get_reader_bookmarks)(5)
    body = json.loads(resp.get_data())
    assert body['bookmarks'][0]['locator'] == 'foliate-locator'
    assert body['bookmarks'][0]['progression'] == .42


@pytest.mark.unit
def test_create_reader_bookmark_clamps_progression_and_commits():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    created = {}
    def make_row(**kwargs):
        row = SimpleNamespace(id=1, created_at=None, updated_at=None, **kwargs)
        created.update(kwargs)
        return row
    mock_ub.ReaderBookmark.side_effect = make_row
    with _ctx('/api/v1/books/5/reader-bookmarks', method='POST', body={
        'format': 'FB2', 'locator': 'foliate-cfi', 'progression': 4, 'chapter': 'Part I',
    }):
        with patch.object(mod, 'current_user', _auth_user()), patch.object(mod, 'ub', mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.create_reader_bookmark)(5)
    assert resp[1] == 201
    assert created['format'] == 'fb2'
    assert created['progression'] == 1.0
    mock_ub.session.commit.assert_called_once()


@pytest.mark.unit
def test_delete_reader_bookmark_is_user_and_book_scoped():
    from cps.api import reader as mod
    row = SimpleNamespace(bookmark_id='cwn-reader-1')
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.filter.return_value.first.return_value = row
    with _ctx('/api/v1/books/5/reader-bookmarks/cwn-reader-1', method='DELETE'):
        with patch.object(mod, 'current_user', _auth_user()), patch.object(mod, 'ub', mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.delete_reader_bookmark)(5, 'cwn-reader-1')
    assert resp[1] == 204
    mock_ub.session.delete.assert_called_once_with(row)
    mock_ub.session.commit.assert_called_once()


@pytest.mark.unit
def test_native_reader_save_queues_moon_writeback_with_text_anchor():
    from cps.api import reader as mod
    user = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1, name="admin")
    body = {
        "format": "FB2",
        "bookmark": "epubcfi(/6/14!/4/2)",
        "position_fraction": .0265,
        "position_anchor": "Рад видеть тебя, Накаяма-сан",
        "device": "cwng-web-test",
    }
    with _ctx("/api/v1/books/5/bookmark", method="POST", body=body):
        with patch.object(mod, "current_user", user), _visible_book(mod), \
             patch.object(mod.deployment_profile, "use_calibre_native_reader_data", return_value=True), \
             patch("cps.services.moonreader_webdav.canonical_fraction_from_anchor", return_value=.02447) as canonical, \
             patch.object(mod, "set_reader_position") as save, \
             patch("cps.tasks.moonreader_sync.queue_moonreader_book_sync") as queue:
            resp = inspect.unwrap(mod.save_bookmark)(5)
    body_out = json.loads(resp.get_data())
    assert body_out["position_fraction"] == pytest.approx(.02447)
    assert body_out["renderer_fraction"] == pytest.approx(.0265)
    canonical.assert_called_once_with(
        1, 5, "fb2", .0265, "Рад видеть тебя, Накаяма-сан",
    )
    assert save.call_args.kwargs["position_fraction"] == pytest.approx(.02447)
    assert save.call_args.kwargs["cfi"] == "epubcfi(/6/14!/4/2)"
    queue.assert_called_once_with(
        1, 5, "fb2", anchor_text="Рад видеть тебя, Накаяма-сан", username="admin",
    )

@pytest.mark.unit
def test_reading_sources_requires_visible_book():
    from cps.api import reader as mod

    with _ctx("/api/v1/books/5/reading-sources"):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=None):
            response = inspect.unwrap(mod.get_reading_sources)(5)

    assert response[1] == 404


@pytest.mark.unit
def test_reading_sources_anonymous_401_before_book_lookup():
    from cps.api import reader as mod

    with _ctx("/api/v1/books/5/reading-sources"):
        with patch.object(
            mod, "current_user",
            SimpleNamespace(is_authenticated=False, is_anonymous=True),
        ), patch.object(mod.calibre_db, "get_filtered_book") as visible:
            response = inspect.unwrap(mod.get_reading_sources)(5)

    assert response[1] == 401
    visible.assert_not_called()


@pytest.mark.unit
def test_reading_sources_matches_hidden_archived_global_detail_visibility():
    from cps.api import reader as mod

    book = SimpleNamespace(
        id=5, title="Book", path="Author/Book (5)", authors=[], data=[],
    )
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.first.return_value = None
    with _ctx("/api/v1/books/5/reading-sources"):
        with patch.object(mod, "current_user", _auth_user(browse_global=True)), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=book) as visible, \
             patch.object(mod.ub, "session", session), \
             patch.object(mod.storyteller_source, "configured_client", return_value=None):
            response = inspect.unwrap(mod.get_reading_sources)(5)

    assert response.status_code == 200
    visible.assert_called_once_with(
        5,
        allow_show_archived=True,
        allow_show_hidden=True,
        allow_show_global=True,
    )


@pytest.mark.unit
def test_reading_sources_returns_devices_and_separate_resolved_carrier(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub
    from cps.api import reader as mod

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    browser = ub.Device(
        user_id=1, kind="webreader", display_name="Browser", active=True,
        created_by="auto",
    )
    other = ub.Device(
        user_id=2, kind="webreader", display_name="Someone else", active=True,
        created_by="auto",
    )
    session.add_all([browser, other])
    session.flush()
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=browser.id, book_id=5, progress_percent=12.5,
            cfi="epubcfi(/6/4!/4/2:4)",
        ),
        ub.DeviceReadingPosition(
            device_id=other.id, book_id=5, progress_percent=99.0,
            cfi="other-user",
        ),
    ])
    state = ub.KoboReadingState(user_id=1, book_id=5)
    state.current_bookmark = ub.KoboBookmark(progress_percent=29.0)
    session.add(state)
    session.commit()

    book = SimpleNamespace(
        id=5, title="Book", path="Author/Book (5)",
        authors=[SimpleNamespace(name="Author")], data=[],
    )
    app = flask.Flask(__name__)
    app.add_url_rule(
        "/api/v1/books/<int:book_id>/reading-sources",
        view_func=inspect.unwrap(mod.get_reading_sources),
    )
    with patch.object(mod, "current_user", _auth_user()), \
         patch.object(mod.calibre_db, "get_filtered_book", return_value=book), \
         patch.object(mod.storyteller_source, "configured_client", return_value=None):
        response = app.test_client().get("/api/v1/books/5/reading-sources")

    assert response.status_code == 200
    assert [row["label"] for row in response.json["sources"]] == [
        "Browser", "Other saved position",
    ]
    assert response.json["sources"][0]["progress_percent"] == 12.5
    assert response.json["sources"][1]["provenance"] == "unknown"
    assert all(row.get("label") != "Someone else" for row in response.json["sources"])
    session.close()
    engine.dispose()
