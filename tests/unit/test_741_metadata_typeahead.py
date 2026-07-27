# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1/metadata/typeahead/<field> — the editor autocomplete
source that stops the new-UI book editor from spawning near-duplicate tags/
series/authors from typos (#741, #778, #689).

Pins: edit-role gating, unknown-field rejection, per-field dispatch to the shared
calibre_db.get_typeahead query (same query the legacy /get_*_json routes use), the
tags tag_filter, the authors/publishers name-normalization, the localized-language
start-first ranking, and the result cap.
"""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="GET"):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context(path, method=method)


def _editor(role_edit=True, anon=False):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon, name="ed",
                           role_edit=lambda: role_edit,
                           role_delete_books=lambda: True, id=1)


# ── role gating ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_typeahead_requires_edit_role():
    from cps.api import edit as mod
    with _ctx("/api/v1/metadata/typeahead/tags?q=sci"):
        with patch.object(mod, "current_user", _editor(role_edit=False)):
            resp = inspect.unwrap(mod.metadata_typeahead)("tags")
    assert resp[1] == 403


@pytest.mark.unit
def test_typeahead_anonymous_401():
    from cps.api import edit as mod
    with _ctx("/api/v1/metadata/typeahead/tags?q=sci"):
        with patch.object(mod, "current_user", _editor(anon=True)):
            resp = inspect.unwrap(mod.metadata_typeahead)("tags")
    assert resp[1] == 401


# ── unknown field ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_typeahead_unknown_field_400():
    from cps.api import edit as mod
    with _ctx("/api/v1/metadata/typeahead/bogus?q=x"):
        with patch.object(mod, "current_user", _editor()):
            resp = inspect.unwrap(mod.metadata_typeahead)("bogus")
    assert resp[1] == 400


# ── per-field dispatch to the shared get_typeahead query ─────────────────────

@pytest.mark.unit
def test_tags_field_uses_tag_filter_and_returns_names():
    from cps.api import edit as mod
    sentinel_filter = object()
    captured = {}

    def fake_typeahead(model, query, replace=("", ""), tag_filter=None):
        captured["model"], captured["query"], captured["tag_filter"] = model, query, tag_filter
        return json.dumps([{"name": "sci-fi"}, {"name": "science"}])

    with _ctx("/api/v1/metadata/typeahead/tags?q=sci"):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(get_typeahead=fake_typeahead)), \
             patch.object(mod, "tags_filters", lambda: sentinel_filter), \
             patch.object(mod.db, "Tags", "TAGS_MODEL"):
            resp = inspect.unwrap(mod.metadata_typeahead)("tags")
    body = json.loads(resp.get_data())
    assert body["field"] == "tags"
    assert body["suggestions"] == ["sci-fi", "science"]
    # tags path must reuse the library's tag visibility filter
    assert captured["tag_filter"] is sentinel_filter
    assert captured["model"] == "TAGS_MODEL"
    assert captured["query"] == "sci"


@pytest.mark.unit
def test_authors_field_scopes_visible_books_and_normalizes_pipe_to_comma():
    from cps.api import edit as mod
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(authors=[SimpleNamespace(name="Le Guin| Ursula K.")])
    ]
    scoped_db = SimpleNamespace(
        session=session,
        common_filters=lambda: object(),
    )
    with _ctx("/api/v1/metadata/typeahead/authors?q=le"):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", scoped_db):
            resp = inspect.unwrap(mod.metadata_typeahead)("authors")
    body = json.loads(resp.get_data())
    assert body["suggestions"] == ["Le Guin, Ursula K."]
    session.query.assert_called_once_with(mod.db.Books)


@pytest.mark.unit
def test_languages_field_ranks_start_matches_first():
    from cps.api import edit as mod
    names = {"en": "English", "eo": "Esperanto", "el": "Greek, Modern", "it": "Italian"}
    with _ctx("/api/v1/metadata/typeahead/languages?q=e"):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "get_locale", return_value="en"), \
             patch.object(mod.isoLanguages, "get_language_names", lambda _loc: names):
            resp = inspect.unwrap(mod.metadata_typeahead)("languages")
    suggestions = json.loads(resp.get_data())["suggestions"]
    # start-matches ("English", "Esperanto") rank before contains-only ("Greek, Modern")
    assert suggestions[:2] == ["English", "Esperanto"]
    assert "Greek, Modern" in suggestions  # matched via the 'e' inside "Greek"
    assert "Italian" not in suggestions     # no 'e'


# ── result cap ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_result_is_capped():
    from cps.api import edit as mod
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(series=[SimpleNamespace(name="series%03d" % i)])
        for i in range(200)
    ]
    scoped_db = SimpleNamespace(session=session, common_filters=lambda: object())
    with _ctx("/api/v1/metadata/typeahead/series?q="):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", scoped_db):
            resp = inspect.unwrap(mod.metadata_typeahead)("series")
    suggestions = json.loads(resp.get_data())["suggestions"]
    assert len(suggestions) == mod._TYPEAHEAD_LIMIT
