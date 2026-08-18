# SPDX-License-Identifier: GPL-3.0-or-later
"""Managed-library cover picker regression tests."""

from __future__ import annotations

import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest


@pytest.mark.unit
def test_cover_picker_apply_selects_calibremcp_path_in_managed_profile():
    from cps import cover_picker

    book = SimpleNamespace(id=527, path="Author/Book (527)")
    sentinel = object()
    app = flask.Flask(__name__)
    with app.test_request_context("/book/527/cover/apply", method="POST", json={"kind": "url", "url": "https://example.test/c.jpg"}):
        with patch.object(cover_picker, "_load_book", return_value=book), \
             patch.object(cover_picker, "_get_lock_state", return_value=False), \
             patch.object(cover_picker.deployment_profile, "is_mcp_managed_library", return_value=True), \
             patch.object(cover_picker, "_apply_managed_cover", return_value=sentinel) as managed:
            result = inspect.unwrap(cover_picker.cover_picker_apply)(527)

    assert result is sentinel
    managed.assert_called_once_with(book)


@pytest.mark.unit
def test_managed_picker_upload_stages_and_delegates_to_calibremcp(tmp_path):
    from cps import cover_picker

    staged = tmp_path / "cover-staged.jpg"
    staged.write_bytes(b"staged-jpeg")
    book = SimpleNamespace(id=527)
    session = MagicMock()
    app = flask.Flask(__name__)
    with app.test_request_context(
        "/book/527/cover/apply",
        method="POST",
        data={"file": (io.BytesIO(b"image"), "cover.png")},
    ):
        with patch.object(cover_picker, "current_user", SimpleNamespace(name="Lion")), \
             patch.object(cover_picker, "stage_cover", return_value=staged) as stage, \
             patch.object(cover_picker, "mcp_update_book_cover") as update, \
             patch.object(cover_picker.calibre_db, "session", session), \
             patch.object(cover_picker.kobo_sync_status, "remove_synced_book"), \
             patch.object(cover_picker.helper, "replace_cover_thumbnail_cache"), \
             patch.object(cover_picker, "url_for", return_value="/cover/527/og"):
            response = cover_picker._apply_managed_cover(book)

    body = json.loads(response.get_data(as_text=True))
    assert body["ok"] is True
    assert body["cover_url"].startswith("/cover/527/og?ts=")
    stage.assert_called_once()
    update.assert_called_once_with("Lion", 527, str(staged))
    session.rollback.assert_called_once()
    session.expire_all.assert_called_once()
    assert not staged.exists(), "staged cover must be removed after CalibreMCP consumes it"


@pytest.mark.unit
def test_managed_picker_embedded_cover_uses_confined_staging(tmp_path):
    from cps import cover_picker

    staged = tmp_path / "cover-staged.jpg"
    staged.write_bytes(b"staged-jpeg")
    book = SimpleNamespace(id=527)
    extracted = SimpleNamespace(data=b"embedded-image")
    app = flask.Flask(__name__)
    with app.test_request_context("/book/527/cover/apply", method="POST", json={"kind": "embedded"}):
        with patch.object(cover_picker, "current_user", SimpleNamespace(name="Lion")), \
             patch.object(cover_picker.cover_extract, "extract_embedded_cover", return_value=extracted), \
             patch.object(cover_picker, "stage_cover_bytes", return_value=staged) as stage, \
             patch.object(cover_picker, "mcp_update_book_cover") as update, \
             patch.object(cover_picker.calibre_db, "session", MagicMock()), \
             patch.object(cover_picker.kobo_sync_status, "remove_synced_book"), \
             patch.object(cover_picker.helper, "replace_cover_thumbnail_cache"), \
             patch.object(cover_picker, "url_for", return_value="/cover/527/og"):
            response = cover_picker._apply_managed_cover(book)

    assert json.loads(response.get_data(as_text=True))["ok"] is True
    stage.assert_called_once_with(b"embedded-image")
    update.assert_called_once_with("Lion", 527, str(staged))
    assert not staged.exists()


@pytest.mark.unit
def test_global_managed_guard_does_not_blanket_block_cover_picker():
    source = Path("cps/__init__.py").read_text(encoding="utf-8")
    blocked = source.split("blocked_blueprints =", 1)[1].split("if request.blueprint", 1)[0]
    assert '"cover_picker"' not in blocked

@pytest.mark.unit
def test_managed_picker_reuses_cached_candidate_bytes(tmp_path):
    from cps import cover_picker

    staged = tmp_path / "cached-cover.jpg"
    staged.write_bytes(b"jpeg")
    book = SimpleNamespace(id=527)
    url = "https://cdn.example.test/cover.jpg"
    app = flask.Flask(__name__)
    with app.test_request_context(
        "/book/527/cover/apply", method="POST", json={"kind": "url", "url": url}
    ):
        with patch.object(cover_picker, "current_user", SimpleNamespace(name="Lion")), \
             patch.object(cover_picker, "_fetch_cache_get", return_value=b"cached-image") as cache_get, \
             patch.object(cover_picker, "stage_cover_bytes", return_value=staged) as stage_bytes, \
             patch.object(cover_picker, "stage_cover") as stage_remote, \
             patch.object(cover_picker, "mcp_update_book_cover"), \
             patch.object(cover_picker.calibre_db, "session", MagicMock()), \
             patch.object(cover_picker.kobo_sync_status, "remove_synced_book"), \
             patch.object(cover_picker.helper, "replace_cover_thumbnail_cache"), \
             patch.object(cover_picker, "url_for", return_value="/cover/527/og"):
            response = cover_picker._apply_managed_cover(book)

    assert json.loads(response.get_data(as_text=True))["ok"] is True
    cache_get.assert_called_once_with(url)
    stage_bytes.assert_called_once_with(b"cached-image")
    stage_remote.assert_not_called()
