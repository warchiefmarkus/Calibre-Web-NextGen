# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""LLM profile management and visible-page translation for the web reader."""
from __future__ import annotations

import json
import os
import uuid

from flask import jsonify, request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from . import api_v1
from .. import calibre_db, logger, ub
from ..cw_login import current_user
from ..services.reader_translation import (
    ReaderTranslationError,
    encrypt_api_key,
    list_models,
    normalize_base_url,
    normalize_endpoint_path,
    normalize_extra_headers,
    serialize_profile,
    test_profile,
    translate_page,
    translation_request_hash,
)
from ..usermanagement import login_required_if_no_ano

log = logger.create()

_ALLOWED_BLOCK_TAGS = {"p", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}
_MAX_BLOCKS = 80
_MAX_BLOCK_CHARS = 8000
_MAX_PAGE_CHARS = 24000
_MAX_PROMPT_CHARS = 6000


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_real_user():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    return None


def _require_visible_book(book_id):
    book = calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True
    )
    if not book:
        return _err("not_found", "Book not found", 404)
    return None


def _profile_query():
    return ub.session.query(ub.ReaderTranslationProfile).filter(
        ub.ReaderTranslationProfile.user_id == int(current_user.id)
    )


def _get_profile(profile_id):
    return _profile_query().filter(
        ub.ReaderTranslationProfile.profile_id == profile_id
    ).first()


def _number(value, *, default, minimum, maximum, integer=False, field="value"):
    if value is None or value == "":
        value = default
    try:
        result = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ReaderTranslationError(
            f"{field} must be a number.", code="invalid_profile", status=400
        ) from exc
    if result < minimum or result > maximum:
        raise ReaderTranslationError(
            f"{field} must be between {minimum} and {maximum}.",
            code="invalid_profile", status=400,
        )
    return result


def _profile_values(payload, current=None):
    if not isinstance(payload, dict):
        raise ReaderTranslationError(
            "LLM profile must be an object.", code="invalid_profile", status=400
        )

    def chosen(key, fallback=None):
        if key in payload:
            return payload[key]
        return getattr(current, key, fallback) if current is not None else fallback

    name = str(chosen("name", "") or "").strip()
    model = str(chosen("model", "") or "").strip()
    if not name or len(name) > 80:
        raise ReaderTranslationError(
            "Profile name is required and must be at most 80 characters.",
            code="invalid_profile", status=400,
        )
    if not model or len(model) > 255:
        raise ReaderTranslationError(
            "Model name is required and must be at most 255 characters.",
            code="invalid_profile", status=400,
        )

    json_mode = chosen("json_mode", True)
    if not isinstance(json_mode, bool):
        raise ReaderTranslationError(
            "json_mode must be true or false.", code="invalid_profile", status=400
        )

    return {
        "name": name,
        "base_url": normalize_base_url(chosen("base_url", "")),
        "endpoint_path": normalize_endpoint_path(chosen("endpoint_path", "chat/completions")),
        "model": model,
        "temperature": _number(
            chosen("temperature", 0.2), default=0.2, minimum=0, maximum=2,
            field="Temperature",
        ),
        "max_output_tokens": _number(
            chosen("max_output_tokens", 4096), default=4096, minimum=64,
            maximum=32768, integer=True, field="Max output tokens",
        ),
        "timeout_seconds": _number(
            chosen("timeout_seconds", 60), default=60, minimum=5,
            maximum=180, integer=True, field="Timeout",
        ),
        "json_mode": json_mode,
        "extra_headers": normalize_extra_headers(chosen("extra_headers", {})),
    }


def _apply_profile_payload(profile, payload):
    for key, value in _profile_values(payload, profile).items():
        setattr(profile, key, value)
    if payload.get("clear_api_key") is True:
        profile.api_key_encrypted = None
    elif "api_key" in payload and str(payload.get("api_key") or "").strip():
        api_key = str(payload["api_key"]).strip()
        if len(api_key) > 4096:
            raise ReaderTranslationError(
                "API key must be at most 4096 characters.",
                code="invalid_profile", status=400,
            )
        profile.api_key_encrypted = encrypt_api_key(api_key)


def _commit_profile(profile, *, created=False):
    try:
        ub.session.commit()
    except IntegrityError:
        ub.session.rollback()
        return _err("duplicate_profile", "A translation profile with this name already exists.", 409)
    except Exception:
        ub.session.rollback()
        log.exception("Could not save reader translation profile")
        return _err("save_failed", "Could not save the translation profile.", 500)
    return jsonify({"profile": serialize_profile(profile)}), 201 if created else 200


@api_v1.route("/reader/translation/profiles")
@login_required_if_no_ano
def get_reader_translation_profiles():
    guard = _require_real_user()
    if guard:
        return guard
    rows = _profile_query().order_by(
        ub.ReaderTranslationProfile.name.asc(),
        ub.ReaderTranslationProfile.id.asc(),
    ).all()
    return jsonify({
        "profiles": [serialize_profile(row) for row in rows],
        "private_endpoints_allowed": os.environ.get(
            "CWNG_READER_TRANSLATION_ALLOW_PRIVATE_ENDPOINTS", "false"
        ).strip().lower() in {"1", "true", "yes", "on"},
    })


@api_v1.route("/reader/translation/profiles", methods=["POST"])
@login_required_if_no_ano
def create_reader_translation_profile():
    guard = _require_real_user()
    if guard:
        return guard
    payload = request.get_json(silent=True)
    try:
        values = _profile_values(payload)
    except ReaderTranslationError as exc:
        return _err(exc.code, str(exc), exc.status)
    profile = ub.ReaderTranslationProfile(
        profile_id=str(uuid.uuid4()),
        user_id=int(current_user.id),
        **values,
    )
    if isinstance(payload, dict) and str(payload.get("api_key") or "").strip():
        api_key = str(payload["api_key"]).strip()
        if len(api_key) > 4096:
            return _err("invalid_profile", "API key must be at most 4096 characters.", 400)
        try:
            profile.api_key_encrypted = encrypt_api_key(api_key)
        except ReaderTranslationError as exc:
            return _err(exc.code, str(exc), exc.status)
    ub.session.add(profile)
    return _commit_profile(profile, created=True)


@api_v1.route("/reader/translation/profiles/<profile_id>", methods=["PATCH"])
@login_required_if_no_ano
def update_reader_translation_profile(profile_id):
    guard = _require_real_user()
    if guard:
        return guard
    profile = _get_profile(profile_id)
    if profile is None:
        return _err("not_found", "Translation profile not found", 404)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("invalid_profile", "LLM profile must be an object.", 400)
    try:
        _apply_profile_payload(profile, payload)
    except ReaderTranslationError as exc:
        return _err(exc.code, str(exc), exc.status)
    return _commit_profile(profile)


@api_v1.route("/reader/translation/profiles/<profile_id>", methods=["DELETE"])
@login_required_if_no_ano
def delete_reader_translation_profile(profile_id):
    guard = _require_real_user()
    if guard:
        return guard
    profile = _get_profile(profile_id)
    if profile is None:
        return _err("not_found", "Translation profile not found", 404)
    ub.session.query(ub.ReaderTranslationCache).filter(
        ub.ReaderTranslationCache.user_id == int(current_user.id),
        ub.ReaderTranslationCache.profile_id == profile_id,
    ).delete(synchronize_session=False)
    view_settings = dict(getattr(current_user, "view_settings", None) or {})
    reader_settings = dict(view_settings.get("reader", {}) or {})
    if reader_settings.get("translationProfileId") == profile_id:
        reader_settings.update({
            "translationProfileId": "",
            "translationEnabled": False,
            "translationView": "original",
        })
        view_settings["reader"] = reader_settings
        current_user.view_settings = view_settings
        flag_modified(current_user, "view_settings")
    ub.session.delete(profile)
    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        return _err("delete_failed", "Could not delete the translation profile.", 500)
    return "", 204


@api_v1.route("/reader/translation/profiles/<profile_id>/test", methods=["POST"])
@login_required_if_no_ano
def test_reader_translation_profile(profile_id):
    guard = _require_real_user()
    if guard:
        return guard
    profile = _get_profile(profile_id)
    if profile is None:
        return _err("not_found", "Translation profile not found", 404)
    try:
        return jsonify(test_profile(profile))
    except ReaderTranslationError as exc:
        return _err(exc.code, str(exc), exc.status)


@api_v1.route("/reader/translation/profiles/<profile_id>/models")
@login_required_if_no_ano
def get_reader_translation_models(profile_id):
    guard = _require_real_user()
    if guard:
        return guard
    profile = _get_profile(profile_id)
    if profile is None:
        return _err("not_found", "Translation profile not found", 404)
    try:
        return jsonify({"models": list_models(profile)})
    except ReaderTranslationError as exc:
        return _err(exc.code, str(exc), exc.status)


def _translation_blocks(payload):
    raw = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_BLOCKS:
        raise ReaderTranslationError(
            f"Page translation requires between 1 and {_MAX_BLOCKS} text blocks.",
            code="invalid_blocks", status=400,
        )
    result = []
    seen = set()
    total_chars = 0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReaderTranslationError("Each page block must be an object.", code="invalid_blocks", status=400)
        block_id = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        tag = str(item.get("tag") or "p").strip().lower()
        if not block_id or len(block_id) > 200 or block_id in seen:
            raise ReaderTranslationError("Page block IDs must be unique and at most 200 characters.", code="invalid_blocks", status=400)
        if not text or len(text) > _MAX_BLOCK_CHARS:
            raise ReaderTranslationError(
                f"Each page block must contain 1 to {_MAX_BLOCK_CHARS} characters.",
                code="invalid_blocks", status=400,
            )
        if tag not in _ALLOWED_BLOCK_TAGS:
            tag = "p"
        seen.add(block_id)
        total_chars += len(text)
        if total_chars > _MAX_PAGE_CHARS:
            raise ReaderTranslationError(
                f"Visible page text exceeds the {_MAX_PAGE_CHARS}-character limit.",
                code="page_too_large", status=413,
            )
        result.append({"id": block_id, "tag": tag, "text": text})
    return result


@api_v1.route("/books/<int:book_id>/translation", methods=["POST"])
@login_required_if_no_ano
def translate_reader_page(book_id):
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("invalid_request", "Translation request must be an object.", 400)

    profile_id = str(payload.get("profile_id") or "").strip()
    profile = _get_profile(profile_id)
    if profile is None:
        return _err("not_found", "Translation profile not found", 404)
    source_language = str(payload.get("source_language") or "auto").strip()[:64] or "auto"
    target_language = str(payload.get("target_language") or "").strip()[:64]
    prompt = str(payload.get("prompt") or "").strip()
    fmt = str(payload.get("format") or "epub").strip().lower()[:16]
    if not target_language:
        return _err("invalid_language", "Target language is required.", 400)
    if len(prompt) > _MAX_PROMPT_CHARS:
        return _err("invalid_prompt", f"Translation prompt must be at most {_MAX_PROMPT_CHARS} characters.", 400)
    try:
        blocks = _translation_blocks(payload)
        request_hash = translation_request_hash(
            profile,
            user_id=int(current_user.id),
            book_id=book_id,
            fmt=fmt,
            source_language=source_language,
            target_language=target_language,
            prompt=prompt,
            blocks=blocks,
        )
    except ReaderTranslationError as exc:
        return _err(exc.code, str(exc), exc.status)

    cached = ub.session.query(ub.ReaderTranslationCache).filter(
        ub.ReaderTranslationCache.user_id == int(current_user.id),
        ub.ReaderTranslationCache.request_hash == request_hash,
    ).first()
    if cached is not None:
        try:
            cached_blocks = json.loads(cached.response_json)
        except (TypeError, ValueError):
            cached_blocks = None
        if isinstance(cached_blocks, list):
            return jsonify({
                "blocks": cached_blocks,
                "cached": True,
                "profile_id": profile.profile_id,
                "model": profile.model,
            })

    try:
        translated = translate_page(
            profile,
            source_language=source_language,
            target_language=target_language,
            prompt=prompt,
            blocks=blocks,
        )
    except ReaderTranslationError as exc:
        return _err(exc.code, str(exc), exc.status)

    cache_row = ub.ReaderTranslationCache(
        user_id=int(current_user.id),
        book_id=book_id,
        format=fmt,
        profile_id=profile.profile_id,
        request_hash=request_hash,
        response_json=json.dumps(translated, ensure_ascii=False),
        source_chars=sum(len(block["text"]) for block in blocks),
    )
    ub.session.add(cache_row)
    try:
        ub.session.commit()
    except IntegrityError:
        # A second tab translated the same page concurrently. The response we
        # already have is valid; discard only the duplicate cache INSERT.
        ub.session.rollback()
    except Exception:
        ub.session.rollback()
        log.exception("Could not cache reader translation")

    return jsonify({
        "blocks": translated,
        "cached": False,
        "profile_id": profile.profile_id,
        "model": profile.model,
    })
