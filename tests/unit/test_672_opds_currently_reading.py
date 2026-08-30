# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behavioral regression coverage for OPDS Currently Reading (#672)."""

from types import SimpleNamespace

import pytest
from werkzeug.exceptions import Forbidden

from cps import app, constants, db, opds, ub


class _User:
    def __init__(self, *, anonymous=False, visible=True):
        self.id = 42
        self.is_anonymous = anonymous
        self.is_authenticated = not anonymous
        self._visible = visible
        self.view_settings = {}

    def check_visibility(self, value):
        if value == constants.SIDEBAR_READ_AND_UNREAD:
            return self._visible
        return True


@pytest.fixture(autouse=True)
def _plain_gettext(monkeypatch):
    plain_defs = {
        key: {**entry, "title": key, "description": key}
        for key, entry in opds.OPDS_ROOT_ENTRY_DEFS.items()
    }
    monkeypatch.setattr(opds, "OPDS_ROOT_ENTRY_DEFS", plain_defs)
    monkeypatch.setattr(opds, "_", lambda value, **_kwargs: value)
    monkeypatch.setattr(
        opds,
        "url_for",
        lambda endpoint: "/opds/currentlyreading"
        if endpoint == "opds.feed_currently_reading" else f"/{endpoint}",
    )


@pytest.mark.unit
def test_root_contains_currently_reading_for_authenticated_visible_user():
    user = _User()
    with app.test_request_context("/opds"):
        entries = opds.get_opds_root_entries(user, allow_anonymous=False)
    current = next(entry for entry in entries if entry["key"] == "currently_reading")
    assert current["title"] == "currently_reading"
    assert current["url"].endswith("/opds/currentlyreading")


@pytest.mark.unit
@pytest.mark.parametrize("user", [_User(anonymous=True), _User(visible=False)])
def test_root_hides_currently_reading_when_read_feeds_are_not_visible(user):
    with app.test_request_context("/opds"):
        keys = {entry["key"] for entry in opds.get_opds_root_entries(user, allow_anonymous=True)}
    assert "currently_reading" not in keys


@pytest.mark.unit
def test_feed_uses_canonical_in_progress_filter_and_restricted_fill(monkeypatch):
    user = _User()
    sentinel_filter = object()
    books = [SimpleNamespace(Books=SimpleNamespace(id=7)), SimpleNamespace(Books=SimpleNamespace(id=9))]
    captured = {}

    def build_filter(rule, user_id=None):
        captured["rule"] = rule
        captured["user_id"] = user_id
        return sentinel_filter

    def fill(*args, **kwargs):
        captured["fill_args"] = args
        return books, False, SimpleNamespace(total_count=2)

    monkeypatch.setattr(opds.auth, "current_user", lambda: user)
    monkeypatch.setattr(opds.magic_shelf, "build_filter_from_rule", build_filter)
    monkeypatch.setattr(opds, "fill_opds_indexpage", fill)
    monkeypatch.setattr(opds, "render_xml_template", lambda *_args, **kwargs: kwargs["entries"])
    monkeypatch.setattr(opds.config, "config_books_per_page", 20, raising=False)
    monkeypatch.setattr(opds.config, "config_read_column", 0, raising=False)

    with app.test_request_context("/opds/currentlyreading"):
        result = opds.feed_currently_reading.__wrapped__()

    assert [row.Books.id for row in result] == [7, 9]
    assert captured["rule"]["value"] == ub.ReadBook.STATUS_IN_PROGRESS
    assert captured["rule"]["operator"] == "equal"
    assert captured["user_id"] == user.id
    assert captured["fill_args"][3] is sentinel_filter


@pytest.mark.unit
@pytest.mark.parametrize("user", [_User(anonymous=True), _User(visible=False)])
def test_feed_rejects_anonymous_or_visibility_restricted_user(monkeypatch, user):
    monkeypatch.setattr(opds.auth, "current_user", lambda: user)
    with app.test_request_context("/opds/currentlyreading"):
        with pytest.raises(Forbidden):
            opds.feed_currently_reading.__wrapped__()


@pytest.mark.unit
def test_currently_reading_navigation_fields_cannot_collapse_into_read_books():
    import inspect

    source = inspect.getsource(opds)
    block = source.split("'currently_reading': {", 1)[1].split("'unread': {", 1)[0]
    assert "'title': N_('Currently Reading')" in block
    assert "'description': N_('Currently Reading')" in block
    assert "N_('Read Books')" not in block


@pytest.mark.unit
def test_dynamic_opds_feeds_are_not_client_cached(monkeypatch):
    from flask import Flask

    mini = Flask(__name__)
    monkeypatch.setattr(opds, "render_template", lambda *args, **kwargs: "<feed/>")
    monkeypatch.setattr(opds.config, "config_calibre_web_title", "Test", raising=False)
    with mini.test_request_context("/opds/currentlyreading"):
        response = opds.render_xml_template("feed.xml", feed_title="Currently Reading")
    cache_control = response.headers.get("Cache-Control", "")
    assert "no-store" in cache_control
    assert "no-cache" in cache_control
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


@pytest.mark.unit
def test_system_currently_reading_magic_shelf_is_not_duplicated_in_opds_navigation():
    user = _User()
    system_current = SimpleNamespace(is_system=True, name="Currently Reading")
    custom_current = SimpleNamespace(is_system=False, name="Currently Reading")

    assert opds._opds_magic_shelf_duplicates_visible_root(system_current, user) is True
    assert opds._opds_magic_shelf_duplicates_visible_root(custom_current, user) is False

    user.view_settings = {"opds": {"hidden_entries": ["currently_reading"]}}
    assert opds._opds_magic_shelf_duplicates_visible_root(system_current, user) is False
