# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 reader bookmark endpoints (auth gate + format casing +
the save/clear write path). DB is mocked; legacy interop (same row, lowercase
format) is the key invariant pinned here."""
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


def _auth_user():
    return SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)


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
def test_get_bookmark_returns_key():
    from cps.api import reader as mod
    row = SimpleNamespace(bookmark_key="epubcfi(/6/4!/4/2)")
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = row
    with _ctx("/api/v1/books/5/bookmark?format=epub"):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod, "ub", mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert resp.status_code == 200
    assert json.loads(resp.get_data())["bookmark"] == "epubcfi(/6/4!/4/2)"


@pytest.mark.unit
def test_get_bookmark_none_when_absent():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx("/api/v1/books/5/bookmark"):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod, "ub", mock_ub), _visible_book(mod):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert json.loads(resp.get_data())["bookmark"] is None


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
def test_get_reader_settings_returns_complete_defaults_plus_saved_values():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {"reader": {"font": "Arial", "margin": 32}}
    with _ctx("/api/v1/reader/settings"):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.get_reader_settings)()
    body = json.loads(resp.get_data())["reader"]
    assert body["font"] == "Arial"
    assert body["margin"] == 32
    assert body["lineHeight"] == 150
    assert body["theme"] == "lightTheme"


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
