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


def test_parse_translation_content_preserves_inline_run_format_and_spacing():
    source = [{
        "id": "a", "tag": "p", "text": "Configure the JTAG clock to 2.5 MHz",
        "runs": [
            {"id": "a-r1", "text": "Configure the "},
            {"id": "a-r2", "text": "JTAG clock", "marks": ["strong"]},
            {"id": "a-r3", "text": " to "},
            {"id": "a-r4", "text": "2.5 MHz", "marks": ["code"], "break_before": 1},
        ],
    }]
    result = parse_translation_content(
        '{"blocks":[{"id":"a","runs":['
        '{"id":"a-r1","text":"Налаштуйте"},'
        '{"id":"a-r2","text":"частоту JTAG"},'
        '{"id":"a-r3","text":"на"},'
        '{"id":"a-r4","text":"2,5 МГц"}]}]}',
        source,
    )
    assert result[0]["runs"] == [
        {"id": "a-r1", "text": "Налаштуйте ",},
        {"id": "a-r2", "text": "частоту JTAG", "marks": ["strong"]},
        {"id": "a-r3", "text": " на "},
        {"id": "a-r4", "text": "2,5 МГц", "marks": ["code"], "break_before": 1},
    ]
    assert result[0]["text"] == "Налаштуйте частоту JTAG на \n2,5 МГц"


def test_parse_translation_content_rejects_missing_inline_run():
    source = [{
        "id": "a", "tag": "p", "text": "A B",
        "runs": [{"id": "r1", "text": "A "}, {"id": "r2", "text": "B"}],
    }]
    with pytest.raises(ReaderTranslationError) as exc:
        parse_translation_content(
            '{"blocks":[{"id":"a","runs":[{"id":"r1","text":"Один"}]}]}',
            source,
        )
    assert exc.value.code == "incomplete_translation"


def test_chat_payload_sends_structured_runs_without_html_or_urls():
    import json
    from cps.services.reader_translation import _chat_payload

    payload = _chat_payload(
        _profile(), source_language="en", target_language="uk", prompt="",
        blocks=[{
            "id": "a", "tag": "p", "text": "Bold link",
            "runs": [
                {"id": "r1", "text": "Bold ", "marks": ["strong"]},
                {"id": "r2", "text": "link", "marks": ["link"]},
            ],
        }], json_mode=True,
    )
    content = payload["messages"][1]["content"]
    page = json.loads(content.split("\n\n", 1)[1])
    assert page["blocks"][0]["runs"] == [
        {"id": "r1", "text": "Bold ", "marks": ["strong"]},
        {"id": "r2", "text": "link", "marks": ["link"]},
    ]
    assert "href" not in content
    assert "<strong>" not in content


def test_gpt_oss_translation_uses_low_reasoning_effort():
    from cps.services.reader_translation import _chat_payload

    payload = _chat_payload(
        _profile(model="openai/gpt-oss-120b"),
        source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "a", "tag": "p", "text": "Source"}], json_mode=True,
    )
    assert payload["reasoning_effort"] == "low"
    assert payload["stream"] is True

    regular = _chat_payload(
        _profile(model="other-model"),
        source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "a", "tag": "p", "text": "Source"}], json_mode=True,
    )
    assert "reasoning_effort" not in regular
    assert regular["stream"] is False


def test_gpt_oss_streaming_completion_is_reassembled(monkeypatch):
    import json
    from cps.services import reader_translation as service

    chunks = [
        {"choices": [{"delta": {"content": '{"blocks":[{"id":"a",'}}]},
        {"choices": [{"delta": {"content": '"text":"Переклад"}]}'}}]},
    ]
    body = "\n\n".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}" for chunk in chunks
    ) + "\n\ndata: [DONE]\n\n"

    class Response:
        ok = True
        status_code = 200
        content = body.encode("utf-8")
        text = body

    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(service, "_request", fake_request)
    profile = _profile(
        model="openai/gpt-oss-120b", api_key_encrypted=None,
        extra_headers={}, timeout_seconds=30,
    )
    result = service.translate_page(
        profile, source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "a", "tag": "p", "text": "Source"}],
    )
    assert result[0]["text"] == "Переклад"
    assert seen["stream"] is True
    assert seen["reasoning_effort"] == "low"


def test_gpt_oss_timeout_is_retried_with_a_bounded_attempt(monkeypatch):
    import json
    from cps.services import reader_translation as service

    event = {
        "choices": [{"delta": {"content": '{"blocks":[{"id":"a","text":"Переклад"}]}'}}],
    }
    body = f"data: {json.dumps(event, ensure_ascii=False)}\n\ndata: [DONE]\n\n"

    class Response:
        ok = True
        status_code = 200
        content = body.encode("utf-8")
        text = body

    timeouts = []

    def fake_request(method, url, **kwargs):
        timeouts.append(kwargs["timeout"])
        if len(timeouts) == 1:
            raise ReaderTranslationError(
                "temporary timeout", code="provider_timeout", status=504,
            )
        return Response()

    monkeypatch.setattr(service, "_request", fake_request)
    profile = _profile(
        model="openai/gpt-oss-120b", api_key_encrypted=None,
        extra_headers={}, timeout_seconds=60,
    )
    result = service.translate_page(
        profile, source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "a", "tag": "p", "text": "Source"}],
    )
    assert result[0]["text"] == "Переклад"
    assert timeouts == [pytest.approx(25.0), pytest.approx(25.0)]


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
    assert translation_request_hash(
        _profile(), **{**base, "blocks": [{
            "id": "a", "tag": "p", "text": "A",
            "runs": [{"id": "r1", "text": "A", "marks": ["strong"]}],
        }]},
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


def test_page_blocks_are_partitioned_by_count_and_character_budget():
    from cps.services.reader_translation import _partition_translation_blocks

    blocks = [
        {"id": f"b{index}", "tag": "p", "text": "x" * 600}
        for index in range(13)
    ]
    batches = _partition_translation_blocks(blocks)
    assert [block["id"] for batch in batches for block in batch] == [block["id"] for block in blocks]
    assert all(len(batch) <= 8 for batch in batches)
    assert all(sum(len(block["text"]) for block in batch) <= 3500 for batch in batches)


def test_incomplete_multi_block_batch_is_retried_as_smaller_batches(monkeypatch):
    from cps.services import reader_translation as service

    calls = []

    def fake_once(profile, *, source_language, target_language, prompt, blocks):
        calls.append([block["id"] for block in blocks])
        if len(blocks) > 1:
            raise ReaderTranslationError(
                "incomplete", code="incomplete_translation", status=502,
            )
        block = blocks[0]
        return [{"id": block["id"], "tag": block["tag"], "text": f"T:{block['text']}"}]

    monkeypatch.setattr(service, "_translate_batch_once", fake_once)
    blocks = [
        {"id": f"b{index}", "tag": "p", "text": f"source {index}"}
        for index in range(4)
    ]
    result = service.translate_page(
        _profile(), source_language="en", target_language="uk", prompt="", blocks=blocks,
    )
    assert [item["id"] for item in result] == [block["id"] for block in blocks]
    assert [item["text"] for item in result] == [f"T:{block['text']}" for block in blocks]
    assert calls[0] == ["b0", "b1", "b2", "b3"]
    assert ["b0"] in calls and ["b3"] in calls


def test_incomplete_formatted_block_falls_back_to_runs_and_rebuilds_marks(monkeypatch):
    from cps.services import reader_translation as service

    calls = []

    def fake_once(profile, *, source_language, target_language, prompt, blocks):
        calls.append([block["id"] for block in blocks])
        if blocks[0].get("runs"):
            raise ReaderTranslationError("incomplete", code="incomplete_translation", status=502)
        return [
            {"id": block["id"], "tag": block["tag"], "text": f"T:{block['text'].strip()}"}
            for block in blocks
        ]

    monkeypatch.setattr(service, "_translate_batch_once", fake_once)
    block = {
        "id": "formatted", "tag": "p", "text": "Open Settings now",
        "runs": [
            {"id": "r1", "text": "Open "},
            {"id": "r2", "text": "Settings", "marks": ["strong"]},
            {"id": "r3", "text": " now", "break_before": 1},
        ],
    }
    result = service.translate_page(
        _profile(), source_language="en", target_language="uk", prompt="", blocks=[block],
    )
    assert calls[0] == ["formatted"]
    assert any(call == ["formatted__cwrun1", "formatted__cwrun2", "formatted__cwrun3"] for call in calls)
    assert result[0]["runs"] == [
        {"id": "r1", "text": "T:Open ",},
        {"id": "r2", "text": "T:Settings", "marks": ["strong"]},
        {"id": "r3", "text": " T:now", "break_before": 1},
    ]


def test_incomplete_long_single_block_is_split_and_reassembled(monkeypatch):
    from cps.services import reader_translation as service

    def fake_once(profile, *, source_language, target_language, prompt, blocks):
        if len(blocks) == 1 and len(blocks[0]["text"]) > 2600:
            raise ReaderTranslationError(
                "incomplete", code="incomplete_translation", status=502,
            )
        return [
            {"id": block["id"], "tag": block["tag"], "text": f"[{block['text']}]"}
            for block in blocks
        ]

    monkeypatch.setattr(service, "_translate_batch_once", fake_once)
    source = "Sentence. " * 700
    result = service.translate_page(
        _profile(), source_language="en", target_language="uk", prompt="",
        blocks=[{"id": "long", "tag": "p", "text": source}],
    )
    assert result[0]["id"] == "long"
    assert result[0]["tag"] == "p"
    assert result[0]["text"].count("[") >= 2
    assert "__cwpart" not in result[0]["id"]


def test_provider_http_is_offloaded_from_the_gevent_request_thread(monkeypatch):
    from cps.services import reader_translation as service

    class Response:
        status_code = 200
        ok = True
        content = b'{}'
        text = '{}'

    calls = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()

    def fake_run_blocking(fn):
        calls.append(('offload', None, None))
        return fn()

    monkeypatch.setattr(service, '_is_trusted_opencode_endpoint', lambda _url: True)
    monkeypatch.setattr(service.requests, 'request', fake_http)
    monkeypatch.setattr(service.parallel, 'run_blocking', fake_run_blocking)

    response = service._request('GET', 'https://opencode.ai/zen/v1/models', timeout=10)

    assert response.status_code == 200
    assert calls[0][0] == 'offload'
    assert calls[1][0:2] == ('GET', 'https://opencode.ai/zen/v1/models')
    assert calls[1][2]['allow_redirects'] is False


def test_partial_translation_retries_only_missing_blocks(monkeypatch):
    from cps.services import reader_translation as service

    calls = []

    def fake_once(profile, *, source_language, target_language, prompt, blocks):
        calls.append([block["id"] for block in blocks])
        if len(blocks) == 3:
            error = ReaderTranslationError(
                "partial", code="incomplete_translation", status=502,
            )
            error.partial_translations = {"a": "TA", "b": "TB"}
            error.missing_block_ids = ["c"]
            raise error
        return [
            {"id": block["id"], "tag": block["tag"], "text": f"T{block['id']}"}
            for block in blocks
        ]

    monkeypatch.setattr(service, "_translate_batch_once", fake_once)
    blocks = [
        {"id": key, "tag": "p", "text": key.upper()}
        for key in ("a", "b", "c")
    ]
    result = service.translate_page(
        _profile(), source_language="en", target_language="uk", prompt="", blocks=blocks,
    )
    assert calls == [["a", "b", "c"], ["c"]]
    assert [item["text"] for item in result] == ["TA", "TB", "Tc"]


def test_big_pickle_uses_small_batches_and_larger_effective_output_budget():
    from cps.services.reader_translation import (
        _effective_max_output_tokens,
        _partition_translation_blocks,
    )

    profile = _profile(model="big-pickle", max_output_tokens=4096)
    blocks = [
        {"id": f"b{index}", "tag": "p", "text": "x" * 500}
        for index in range(7)
    ]
    batches = _partition_translation_blocks(blocks, profile=profile)
    assert all(len(batch) <= 2 for batch in batches)
    assert all(sum(len(item["text"]) for item in batch) <= 1200 for batch in batches)
    assert _effective_max_output_tokens(profile) == 8192


def test_model_catalog_preserves_available_metadata(monkeypatch):
    from cps.services import reader_translation as service

    class Response:
        ok = True
        status_code = 200
        content = b'{}'
        text = '{}'

        @staticmethod
        def json():
            return {
                "object": "list",
                "data": [
                    {"id": "nvidia/model-b", "owned_by": "nvidia"},
                    {"id": "nvidia/model-a", "owned_by": "nvidia", "max_model_len": 131072},
                ],
            }

    monkeypatch.setattr(service, "_request", lambda *args, **kwargs: Response())
    monkeypatch.setattr(service, "_profile_headers", lambda profile: {})
    profile = _profile(timeout_seconds=30)
    catalog = service.list_model_catalog(profile)
    assert catalog == [
        {"id": "nvidia/model-a", "owner": "nvidia", "context_length": 131072},
        {"id": "nvidia/model-b", "owner": "nvidia"},
    ]


def test_only_exact_https_nvidia_nim_routes_bypass_generic_ssrf_resolver():
    from cps.services.reader_translation import _is_trusted_nvidia_endpoint

    assert _is_trusted_nvidia_endpoint("https://integrate.api.nvidia.com/v1/models")
    assert _is_trusted_nvidia_endpoint("https://integrate.api.nvidia.com/v1/chat/completions")
    assert not _is_trusted_nvidia_endpoint("http://integrate.api.nvidia.com/v1/models")
    assert not _is_trusted_nvidia_endpoint("https://evil.integrate.api.nvidia.com/v1/models")
    assert not _is_trusted_nvidia_endpoint("https://integrate.api.nvidia.com.evil.test/v1/models")
    assert not _is_trusted_nvidia_endpoint("https://integrate.api.nvidia.com/other/models")


def test_page_translation_deadline_limits_all_fallback_requests(monkeypatch):
    from cps.services import reader_translation as service

    profile = _profile(timeout_seconds=60)
    service._TRANSLATION_CONTEXT.deadline = 100.0
    try:
        monkeypatch.setattr(service.time, "monotonic", lambda: 90.0)
        assert service._remaining_translation_timeout(profile) == pytest.approx(10.0)

        monkeypatch.setattr(service.time, "monotonic", lambda: 100.01)
        with pytest.raises(ReaderTranslationError) as exc:
            service._remaining_translation_timeout(profile)
        assert exc.value.code == "translation_timeout"
        assert exc.value.status == 504
    finally:
        try:
            del service._TRANSLATION_CONTEXT.deadline
        except AttributeError:
            pass


def test_translate_page_reuses_one_deadline_during_incomplete_retry(monkeypatch):
    from cps.services import reader_translation as service

    deadlines = []
    calls = 0

    def fake_once(profile, *, source_language, target_language, prompt, blocks):
        nonlocal calls
        calls += 1
        deadlines.append(service._TRANSLATION_CONTEXT.deadline)
        if calls == 1:
            raise ReaderTranslationError(
                "incomplete", code="incomplete_translation", status=502,
            )
        return [
            {"id": block["id"], "tag": block["tag"], "text": f"T:{block['text']}"}
            for block in blocks
        ]

    monkeypatch.setattr(service, "_translate_batch_once", fake_once)
    result = service.translate_page(
        _profile(timeout_seconds=60), source_language="en", target_language="uk",
        prompt="", blocks=[
            {"id": "a", "tag": "p", "text": "A"},
            {"id": "b", "tag": "p", "text": "B"},
        ],
    )
    assert [item["text"] for item in result] == ["T:A", "T:B"]
    assert len(deadlines) >= 2
    assert len(set(deadlines)) == 1


def test_visible_page_is_sent_as_one_initial_request(monkeypatch):
    from cps.services import reader_translation as service

    calls = []

    def fake_once(profile, *, source_language, target_language, prompt, blocks):
        calls.append([block["id"] for block in blocks])
        return [
            {"id": block["id"], "tag": block["tag"], "text": f"T:{block['text']}"}
            for block in blocks
        ]

    monkeypatch.setattr(service, "_translate_batch_once", fake_once)
    blocks = [
        {"id": f"b{index}", "tag": "p", "text": "source"}
        for index in range(9)
    ]
    result = service.translate_page(
        _profile(model="openai/gpt-oss-120b", timeout_seconds=60),
        source_language="en", target_language="uk", prompt="", blocks=blocks,
    )
    assert calls == [[f"b{index}" for index in range(9)]]
    assert [item["id"] for item in result] == [f"b{index}" for index in range(9)]


def test_provider_timeout_splits_the_page_only_as_fallback(monkeypatch):
    from cps.services import reader_translation as service

    calls = []

    def fake_once(profile, *, source_language, target_language, prompt, blocks):
        ids = [block["id"] for block in blocks]
        calls.append(ids)
        if len(blocks) == 6:
            raise ReaderTranslationError(
                "timeout", code="provider_timeout", status=504,
            )
        return [
            {"id": block["id"], "tag": block["tag"], "text": f"T:{block['text']}"}
            for block in blocks
        ]

    monkeypatch.setattr(service, "_translate_batch_once", fake_once)
    blocks = [
        {"id": f"b{index}", "tag": "p", "text": "source"}
        for index in range(6)
    ]
    result = service.translate_page(
        _profile(model="openai/gpt-oss-120b", timeout_seconds=60),
        source_language="en", target_language="uk", prompt="", blocks=blocks,
    )
    assert calls == [
        [f"b{index}" for index in range(6)],
        ["b0", "b1", "b2"],
        ["b3", "b4", "b5"],
    ]
    assert [item["id"] for item in result] == [f"b{index}" for index in range(6)]
