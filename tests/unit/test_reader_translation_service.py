# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace

import pytest

from cps.services.reader_translation import (
    ReaderTranslationError,
    normalize_base_url,
    normalize_extra_headers,
    parse_translation_content,
    translation_request_hash,
)

pytestmark = pytest.mark.unit


def _profile(**overrides):
    values = {
        "base_url": "https://example.test/v1",
        "endpoint_path": "chat/completions",
        "model": "test-model",
        "temperature": 0.2,
        "max_output_tokens": 2048,
        "json_mode": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_translation_content_preserves_source_order_and_tags():
    source = [
        {"id": "a", "tag": "h2", "text": "Title"},
        {"id": "b", "tag": "p", "text": "Body"},
    ]
    result = parse_translation_content(
        '```json\n{"blocks":[{"id":"b","text":"Тіло"},{"id":"a","text":"Заголовок"}]}\n```',
        source,
    )
    assert result == [
        {"id": "a", "tag": "h2", "text": "Заголовок"},
        {"id": "b", "tag": "p", "text": "Тіло"},
    ]


def test_parse_translation_content_rejects_missing_blocks():
    with pytest.raises(ReaderTranslationError) as exc:
        parse_translation_content(
            '{"blocks":[{"id":"a","text":"Only one"}]}',
            [{"id": "a", "tag": "p", "text": "A"}, {"id": "b", "tag": "p", "text": "B"}],
        )
    assert exc.value.code == "incomplete_translation"


def test_request_hash_changes_with_text_language_model_or_prompt():
    base = dict(
        user_id=1, book_id=2, fmt="epub", source_language="auto",
        target_language="uk", prompt="", blocks=[{"id": "a", "tag": "p", "text": "A"}],
    )
    original = translation_request_hash(_profile(), **base)
    assert translation_request_hash(_profile(model="other"), **base) != original
    assert translation_request_hash(_profile(), **{**base, "target_language": "pl"}) != original
    assert translation_request_hash(_profile(), **{**base, "prompt": "Literary"}) != original
    assert translation_request_hash(
        _profile(), **{**base, "blocks": [{"id": "a", "tag": "p", "text": "B"}]},
    ) != original


def test_base_url_and_headers_block_credential_and_authorization_overrides():
    assert normalize_base_url("https://api.example/v1/") == "https://api.example/v1"
    with pytest.raises(ReaderTranslationError):
        normalize_base_url("https://user:pass@example.test/v1")
    with pytest.raises(ReaderTranslationError):
        normalize_extra_headers({"Authorization": "other secret"})
    assert normalize_extra_headers({"HTTP-Referer": "https://reader.example"}) == {
        "HTTP-Referer": "https://reader.example"
    }


def test_custom_prompt_replaces_only_language_placeholders_and_keeps_json_braces():
    from cps.services.reader_translation import _chat_payload
    payload = _chat_payload(
        _profile(), source_language="en", target_language="uk",
        prompt='Translate {source_language} to {target_language}. Return {"ok": true}.',
        blocks=[{"id": "a", "tag": "p", "text": "A"}], json_mode=True,
    )
    instruction = payload["messages"][1]["content"]
    assert 'Translate English to Ukrainian. Return {"ok": true}.' in instruction


def test_responses_api_payload_and_output_are_supported(monkeypatch):
    import json
    from cps.services import reader_translation as service

    profile = _profile(endpoint_path="responses")
    profile.api_key_encrypted = None
    profile.extra_headers = {}
    profile.timeout_seconds = 30
    seen = {}

    class Response:
        status_code = 200
        ok = True
        text = ""
        content = b"{}"

        @staticmethod
        def json():
            return {
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": json.dumps({"blocks": [{"id": "a", "text": "Переклад"}]}, ensure_ascii=False),
                    }],
                }],
            }

    def fake_request(method, url, **kwargs):
        seen.update({"method": method, "url": url, "json": kwargs["json"]})
        return Response()

    monkeypatch.setattr(service, "_request", fake_request)
    result = service.translate_page(
        profile, source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "a", "tag": "p", "text": "Source"}],
    )
    assert result[0]["text"] == "Переклад"
    assert seen["url"].endswith("/responses")
    assert seen["json"]["instructions"]
    assert seen["json"]["input"]
    assert seen["json"]["max_output_tokens"] == 2048
    assert "messages" not in seen["json"]


def test_anthropic_messages_payload_headers_and_response_are_supported(monkeypatch):
    import json
    from cps.services import reader_translation as service

    profile = _profile(endpoint_path="messages", model="claude-sonnet-4-6")
    profile.api_key_encrypted = "encrypted"
    profile.extra_headers = {}
    profile.timeout_seconds = 30
    seen = {}

    class Response:
        status_code = 200
        ok = True
        text = ""
        content = b"{}"

        @staticmethod
        def json():
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "blocks": [{"id": "a", "text": "Переклад"}],
                    }, ensure_ascii=False),
                }],
            }

    def fake_request(method, url, **kwargs):
        seen.update({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr(service, "decrypt_api_key", lambda _value: "zen-key")
    monkeypatch.setattr(service, "_request", fake_request)
    result = service.translate_page(
        profile, source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "a", "tag": "p", "text": "Source"}],
    )
    assert result[0]["text"] == "Переклад"
    assert seen["url"].endswith("/messages")
    assert seen["headers"]["Authorization"] == "Bearer zen-key"
    assert seen["headers"]["x-api-key"] == "zen-key"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["json"]["system"]
    assert seen["json"]["messages"][0]["role"] == "user"


def test_google_generate_content_payload_headers_and_response_are_supported(monkeypatch):
    import json
    from cps.services import reader_translation as service

    profile = _profile(
        endpoint_path="models/gemini-3.5-flash:generateContent",
        model="gemini-3.5-flash",
    )
    profile.api_key_encrypted = "encrypted"
    profile.extra_headers = {}
    profile.timeout_seconds = 30
    seen = {}

    class Response:
        status_code = 200
        ok = True
        text = ""
        content = b"{}"

        @staticmethod
        def json():
            return {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "blocks": [{"id": "a", "text": "Переклад"}],
                            }, ensure_ascii=False),
                        }],
                    },
                }],
            }

    def fake_request(method, url, **kwargs):
        seen.update({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr(service, "decrypt_api_key", lambda _value: "zen-key")
    monkeypatch.setattr(service, "_request", fake_request)
    result = service.translate_page(
        profile, source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "a", "tag": "p", "text": "Source"}],
    )
    assert result[0]["text"] == "Переклад"
    assert seen["url"].endswith("/models/gemini-3.5-flash:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "zen-key"
    assert seen["json"]["systemInstruction"]["parts"][0]["text"]
    assert seen["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_only_exact_https_opencode_zen_routes_bypass_generic_ssrf_resolver():
    from cps.services.reader_translation import _is_trusted_opencode_endpoint

    assert _is_trusted_opencode_endpoint("https://opencode.ai/zen/v1/models")
    assert _is_trusted_opencode_endpoint("https://opencode.ai/zen/go/v1/chat/completions")
    assert not _is_trusted_opencode_endpoint("http://opencode.ai/zen/v1/models")
    assert not _is_trusted_opencode_endpoint("https://evil.opencode.ai/zen/v1/models")
    assert not _is_trusted_opencode_endpoint("https://opencode.ai.evil.test/zen/v1/models")
    assert not _is_trusted_opencode_endpoint("https://opencode.ai/docs/zen")
