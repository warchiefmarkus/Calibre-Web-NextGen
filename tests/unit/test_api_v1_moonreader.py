# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

pytestmark = pytest.mark.unit


def _ctx(path, body=None):
    app = flask.Flask(__name__)
    kwargs = {"method": "POST" if body is not None else "GET"}
    if body is not None:
        kwargs["json"] = body
    return app.test_request_context(path, **kwargs)


def _user():
    return SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1, name="alice")


def _row(**changes):
    values = dict(
        enabled=True, base_url="http://host/books/", username="reader",
        password_encrypted="encrypted", cache_path="", last_test_at=None,
        last_test_status=None, last_test_error=None, last_sync_at=None,
        sync_status="idle", last_sync_error=None, last_sync_summary={},
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_save_moonreader_password_is_encrypted_and_never_serialized():
    from cps.api import moonreader as mod
    row = _row(password_encrypted=None)
    session = MagicMock()
    with _ctx("/api/v1/account/moonreader", {
        "enabled": True, "base_url": "http://host/books", "username": "reader",
        "password": "secret", "cache_path": ".Moon+/Cache",
    }), patch.object(mod, "current_user", _user()), \
         patch.object(mod, "_row", return_value=row), \
         patch.object(mod, "encrypt_password", return_value="ciphertext") as encrypt, \
         patch.object(mod.ub, "session", session):
        response = inspect.unwrap(mod.save_moonreader_settings)()
    body = json.loads(response.get_data())
    assert row.password_encrypted == "ciphertext"
    assert body["password_configured"] is True
    assert "password" not in body
    encrypt.assert_called_once_with("secret")
    session.commit.assert_called_once()


def test_connection_test_uses_stored_password_without_returning_it():
    from cps.api import moonreader as mod
    row = _row()
    session = MagicMock()
    expected = {"ok": True, "cache_found": False, "position_files": 0,
                "base_url": row.base_url, "cache_path": ".Moon+/Cache"}
    with _ctx("/api/v1/account/moonreader/test", {}), \
         patch.object(mod, "current_user", _user()), \
         patch.object(mod, "_row", return_value=row), \
         patch.object(mod, "decrypt_password", return_value="secret"), \
         patch.object(mod, "test_connection", return_value=expected) as test, \
         patch.object(mod.ub, "session", session):
        response = inspect.unwrap(mod.test_moonreader_connection)()
    body = json.loads(response.get_data())
    assert body["test"] == expected
    assert "password" not in body
    test.assert_called_once_with(base_url=row.base_url, username="reader",
                                 password="secret", cache_path="")


def test_sync_requires_enabled_connection_and_queues_hidden_task():
    from cps.api import moonreader as mod
    row = _row()
    with _ctx("/api/v1/account/moonreader/sync", {}), \
         patch.object(mod, "current_user", _user()), \
         patch.object(mod, "_row", return_value=row), \
         patch.object(mod, "queue_moonreader_sync", return_value={"queued": True}) as queue:
        response, status = inspect.unwrap(mod.start_moonreader_sync)()
    assert status == 202
    assert json.loads(response.get_data())["queued"] is True
    queue.assert_called_once_with(1, "alice")
