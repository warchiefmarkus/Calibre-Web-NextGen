# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

pytestmark = pytest.mark.unit


def _ctx(path, method="GET", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _user():
    return SimpleNamespace(id=7, is_authenticated=True, is_anonymous=False)


def _profile(**overrides):
    values = dict(
        id=1, profile_id="profile-1", user_id=7, name="Provider",
        base_url="https://api.example/v1", endpoint_path="chat/completions",
        api_key_encrypted="encrypted", model="model-a", temperature=0.2,
        max_output_tokens=4096, timeout_seconds=60, json_mode=True,
        extra_headers={}, created_at=None, updated_at=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _visible(mod):
    return patch.object(mod.calibre_db, "get_filtered_book", return_value=SimpleNamespace(id=5))


def test_profile_serialization_never_returns_key_material():
    from cps.services.reader_translation import serialize_profile
    body = serialize_profile(_profile(api_key_encrypted="ciphertext-secret"))
    assert body["has_api_key"] is True
    assert "api_key" not in body
    assert "ciphertext-secret" not in json.dumps(body)


def test_create_profile_encrypts_api_key_and_returns_redacted_profile():
    from cps.api import reader_translation as mod
    mock_ub = MagicMock()

    def make_profile(**kwargs):
        return SimpleNamespace(
            id=1, created_at=None, updated_at=None, api_key_encrypted=None,
            **kwargs,
        )

    mock_ub.ReaderTranslationProfile.side_effect = make_profile
    body = {
        "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
        "endpoint_path": "chat/completions", "api_key": "clear-secret",
        "model": "free-model", "temperature": 0.1,
        "max_output_tokens": 2048, "timeout_seconds": 45,
        "json_mode": True, "extra_headers": {},
    }
    with _ctx("/api/v1/reader/translation/profiles", method="POST", body=body):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "encrypt_api_key", return_value="ciphertext"):
            response, status = inspect.unwrap(mod.create_reader_translation_profile)()
    payload = json.loads(response.get_data())
    assert status == 201
    assert payload["profile"]["has_api_key"] is True
    assert "clear-secret" not in response.get_data(as_text=True)
    assert mock_ub.session.add.call_args.args[0].api_key_encrypted == "ciphertext"


def test_translate_page_returns_server_cache_without_calling_provider():
    from cps.api import reader_translation as mod
    cached = SimpleNamespace(response_json=json.dumps([
        {"id": "a", "tag": "p", "text": "Переклад"},
    ]))
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = cached
    request_body = {
        "profile_id": "profile-1", "format": "epub",
        "source_language": "en", "target_language": "uk", "prompt": "",
        "blocks": [{"id": "a", "tag": "p", "text": "Source"}],
    }
    with _ctx("/api/v1/books/5/translation", method="POST", body=request_body):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "_get_profile", return_value=_profile()), _visible(mod), \
             patch.object(mod, "translate_page") as provider:
            response = inspect.unwrap(mod.translate_reader_page)(5)
    payload = json.loads(response.get_data())
    assert payload["cached"] is True
    assert payload["blocks"][0]["text"] == "Переклад"
    provider.assert_not_called()


def test_translate_page_calls_provider_and_writes_cache():
    from cps.api import reader_translation as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    created = {}

    def make_cache(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(**kwargs)

    mock_ub.ReaderTranslationCache.side_effect = make_cache
    translated = [{"id": "a", "tag": "p", "text": "Переклад"}]
    request_body = {
        "profile_id": "profile-1", "format": "EPUB",
        "source_language": "en", "target_language": "uk", "prompt": "Literary",
        "blocks": [{"id": "a", "tag": "p", "text": "Source"}],
    }
    with _ctx("/api/v1/books/5/translation", method="POST", body=request_body):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "_get_profile", return_value=_profile()), _visible(mod), \
             patch.object(mod, "translate_page", return_value=translated) as provider:
            response = inspect.unwrap(mod.translate_reader_page)(5)
    payload = json.loads(response.get_data())
    assert payload["cached"] is False
    assert created["format"] == "epub"
    assert json.loads(created["response_json"]) == translated
    mock_ub.session.add.assert_called_once()
    mock_ub.session.commit.assert_called_once()
    provider.assert_called_once()


def test_translate_page_rejects_duplicate_block_ids_before_provider_call():
    from cps.api import reader_translation as mod
    request_body = {
        "profile_id": "profile-1", "target_language": "uk",
        "blocks": [
            {"id": "same", "tag": "p", "text": "A"},
            {"id": "same", "tag": "p", "text": "B"},
        ],
    }
    with _ctx("/api/v1/books/5/translation", method="POST", body=request_body):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "_get_profile", return_value=_profile()), _visible(mod), \
             patch.object(mod, "translate_page") as provider:
            response, status = inspect.unwrap(mod.translate_reader_page)(5)
    assert status == 400
    assert json.loads(response.get_data())["error"]["code"] == "invalid_blocks"
    provider.assert_not_called()


def test_translate_page_skips_provider_when_source_matches_target():
    from cps.api import reader_translation as mod

    request_body = {
        "profile_id": "profile-1", "format": "fb2",
        "source_language": "uk-UA", "target_language": "uk", "prompt": "",
        "blocks": [{"id": "a", "tag": "p", "text": "Український текст сторінки."}],
    }
    with _ctx("/api/v1/books/5/translation", method="POST", body=request_body):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "_get_profile", return_value=_profile()), _visible(mod), \
             patch.object(mod, "translate_page") as provider:
            response = inspect.unwrap(mod.translate_reader_page)(5)
    payload = json.loads(response.get_data())
    assert payload["skipped"] is True
    assert payload["blocks"] == request_body["blocks"]
    provider.assert_not_called()


def test_translate_page_rejects_more_than_one_visible_page_of_text():
    from cps.api import reader_translation as mod

    request_body = {
        "profile_id": "profile-1", "target_language": "uk",
        "blocks": [
            {"id": "a", "tag": "p", "text": "x" * 4001},
            {"id": "b", "tag": "p", "text": "y" * 4001},
        ],
    }
    with _ctx("/api/v1/books/5/translation", method="POST", body=request_body):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "_get_profile", return_value=_profile()), _visible(mod), \
             patch.object(mod, "translate_page") as provider:
            response, status = inspect.unwrap(mod.translate_reader_page)(5)
    assert status == 413
    assert json.loads(response.get_data())["error"]["code"] == "page_too_large"
    provider.assert_not_called()


def test_translate_page_cache_disabled_ignores_existing_cache_and_does_not_write():
    from cps.api import reader_translation as mod

    cached = SimpleNamespace(response_json=json.dumps([
        {"id": "a", "tag": "p", "text": "Старий кеш"},
    ]))
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = cached
    translated = [{"id": "a", "tag": "p", "text": "Новий переклад"}]
    request_body = {
        "profile_id": "profile-1", "format": "epub",
        "source_language": "en", "target_language": "uk", "prompt": "",
        "cache_enabled": False,
        "blocks": [{"id": "a", "tag": "p", "text": "Source"}],
    }
    with _ctx("/api/v1/books/5/translation", method="POST", body=request_body):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "_get_profile", return_value=_profile()), _visible(mod), \
             patch.object(mod, "translate_page", return_value=translated) as provider:
            response = inspect.unwrap(mod.translate_reader_page)(5)
    payload = json.loads(response.get_data())
    assert payload["cached"] is False
    assert payload["blocks"] == translated
    provider.assert_called_once()
    mock_ub.session.query.assert_not_called()
    mock_ub.session.add.assert_not_called()
    mock_ub.session.commit.assert_not_called()


def test_translate_page_rejects_non_boolean_cache_setting():
    from cps.api import reader_translation as mod

    request_body = {
        "profile_id": "profile-1", "target_language": "uk",
        "cache_enabled": "false",
        "blocks": [{"id": "a", "tag": "p", "text": "Source"}],
    }
    with _ctx("/api/v1/books/5/translation", method="POST", body=request_body):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "_get_profile", return_value=_profile()), _visible(mod), \
             patch.object(mod, "translate_page") as provider:
            response, status = inspect.unwrap(mod.translate_reader_page)(5)
    assert status == 400
    assert json.loads(response.get_data())["error"]["code"] == "invalid_cache_setting"
    provider.assert_not_called()
