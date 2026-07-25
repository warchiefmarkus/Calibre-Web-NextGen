# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from cps import annotations
from cps.api import edit, rag


def _unwrapped(function):
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


@pytest.mark.unit
def test_rag_empty_visibility_short_circuits_backend(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr(rag.deployment_profile, "enable_rag_ui", lambda: True)
    monkeypatch.setattr(
        rag, "current_user",
        SimpleNamespace(is_authenticated=True, is_anonymous=False, id=7, name="reader"),
    )
    monkeypatch.setattr(rag, "_visible_book_ids", lambda: set())
    monkeypatch.setattr(
        rag, "search_rag",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("backend called")),
    )
    with app.test_request_context(
        "/api/v1/rag/search", method="POST", json={"query": "test query"}
    ):
        response = _unwrapped(rag.rag_search)()
    payload = response.get_json()
    assert payload["count"] == 0
    assert payload["results"] == []


@pytest.mark.unit
def test_metadata_typeahead_uses_only_visible_books(monkeypatch):
    visible = [SimpleNamespace(
        authors=[SimpleNamespace(name="Visible Author")],
        publishers=[SimpleNamespace(name="Visible Publisher")],
        series=[SimpleNamespace(name="Visible Series")],
    )]

    class Query:
        def filter(self, *_args):
            return self

        def all(self):
            return visible

    monkeypatch.setattr(
        edit.calibre_db,
        "session",
        SimpleNamespace(query=lambda *_args: Query()),
    )
    monkeypatch.setattr(edit.calibre_db, "common_filters", lambda: True)
    assert edit._typeahead_names("authors", "visible") == ["Visible Author"]
    assert edit._typeahead_names("publishers", "visible") == ["Visible Publisher"]
    assert edit._typeahead_names("series", "visible") == ["Visible Series"]


@pytest.mark.unit
def test_annotations_allow_user_hidden_book(monkeypatch):
    calls = {}
    book = object()

    def get_filtered_book(book_id, **kwargs):
        calls.update(kwargs)
        return book

    monkeypatch.setattr(annotations.calibre_db, "get_filtered_book", get_filtered_book)
    assert annotations._resolve_book_or_404(10) is book
    assert calls["allow_show_archived"] is True
    assert calls["allow_show_hidden"] is True
