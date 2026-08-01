# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authenticated, browser-safe CalibreMCP RAG proxy."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request

from . import api_v1
from .. import calibre_db, db, deployment_profile, limiter
from ..cw_login import current_user
from ..services.calibremcp_client import (
    CalibreMCPClientError,
    get_book_ocr_status,
    get_rag_ocr_config,
    get_rag_status,
    queue_book_ocr,
    search_rag,
    update_rag_ocr_config,
)


def _err(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _guard():
    if not deployment_profile.enable_rag_ui():
        return _err("feature_disabled", "RAG search is disabled", 404)
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("forbidden", "RAG search requires a signed-in user", 403)
    return None


def _rag_edit_guard():
    guard = _guard()
    if guard:
        return guard
    if not current_user.role_edit():
        return _err("forbidden", "RAG configuration requires edit permission", 403)
    return None


def _ocr_guard(book_id: int):
    guard = _guard()
    if guard:
        return guard
    if not current_user.role_edit():
        return _err("forbidden", "OCR requires edit permission", 403)
    if int(book_id) not in _visible_book_ids():
        return _err("not_found", "Book not found", 404)
    return None


def _ocr_response(raw: dict[str, Any]) -> dict[str, Any]:
    result = raw.get("result") if isinstance(raw.get("result"), dict) else None
    safe_result = None
    if result is not None:
        safe_result = {
            key: result.get(key)
            for key in (
                "status", "book_id", "source_format", "page_count", "text_pages", "extracted_chars",
                "output_size", "cached", "duration_seconds", "engine",
                "engine_version", "languages", "reindex_job_id",
            )
            if key in result
        }
    reindex = raw.get("reindex_job") if isinstance(raw.get("reindex_job"), dict) else None
    safe_reindex = None
    if reindex is not None:
        safe_reindex = {
            key: reindex.get(key)
            for key in (
                "job_id", "book_id", "operation", "status", "attempts",
                "queued_at", "started_at", "finished_at", "error", "result",
            )
            if key in reindex
        }
    return {
        key: raw.get(key)
        for key in (
            "success", "accepted", "created", "job_id", "book_id", "title",
            "status", "ocr_job_status", "stage", "attempts", "queued_at",
            "started_at", "finished_at", "error", "terminal", "page_count",
            "max_pages", "force", "retry", "status_operation",
        )
        if key in raw
    } | {
        "result": safe_result,
        "reindex_job": safe_reindex,
    }


def _user_name() -> str:
    return str(getattr(current_user, "name", "") or "").strip()


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _string_list(value: Any, *, limit: int = 50) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("filter must be an array")
    items = []
    for item in value[:limit]:
        text = str(item).strip()
        if text:
            items.append(text[:200])
    return items or None


def _book_ids(value: Any) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("book_ids must be an array")
    result = []
    for item in value[:100]:
        try:
            book_id = int(item)
        except (TypeError, ValueError):
            raise ValueError("book_ids must contain integers")
        if book_id > 0:
            result.append(book_id)
    return result or None


def _visible_book_ids() -> set[int]:
    rows = (calibre_db.session.query(db.Books.id)
            .filter(calibre_db.common_filters())
            .all())
    return {int(row[0]) for row in rows}


def _result_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": item.get("chunk_id"),
        "book_id": item.get("book_id"),
        "ordinal": item.get("ordinal"),
        "title": _clip(item.get("title"), 300),
        "authors": [str(v)[:200] for v in (item.get("authors") or [])[:20]],
        "format": _clip(item.get("format"), 20),
        "chapter": _clip(item.get("chapter"), 300),
        "section": _clip(item.get("section"), 300),
        "page_start": item.get("page_start"),
        "page_end": item.get("page_end"),
        "text": _clip(item.get("text"), 2800),
        "context_before": _clip(item.get("context_before"), 900),
        "context_after": _clip(item.get("context_after"), 900),
        "semantic_score": item.get("semantic_score"),
        "keyword_score": item.get("keyword_score"),
        "evidence_score": item.get("evidence_score"),
        "proximity_score": item.get("proximity_score"),
        "lexical_rank": item.get("lexical_rank"),
        "matched_terms": [str(v)[:100] for v in (item.get("matched_terms") or [])[:24]],
        "reranker_score": item.get("reranker_score"),
        "reranker_rank": item.get("reranker_rank"),
        "combined_score": item.get("combined_score"),
    }


@api_v1.route("/rag/status")
def rag_status():
    guard = _guard()
    if guard:
        return guard
    try:
        raw = get_rag_status(_user_name())
    except CalibreMCPClientError as exc:
        return _err("rag_backend_error", str(exc), exc.status_code)
    statuses = raw.get("statuses") if isinstance(raw.get("statuses"), dict) else {}
    vector = raw.get("vector_store") if isinstance(raw.get("vector_store"), dict) else {}
    raw_activity = raw.get("activity") if isinstance(raw.get("activity"), dict) else {}
    visible_ids = _visible_book_ids()
    active_jobs = [
        {
            key: item.get(key)
            for key in (
                "job_id", "book_id", "title", "operation", "status", "stage",
                "selected_format", "queued_at", "started_at", "page_count", "max_pages",
            )
        }
        for item in (raw_activity.get("active_jobs") or [])[:20]
        if isinstance(item, dict) and int(item.get("book_id") or 0) in visible_ids
    ]
    return jsonify({
        "enabled": bool(raw.get("enabled")),
        "ready": bool(
            raw.get("enabled")
            and raw.get("model_ready")
            and vector.get("current")
            and (not raw.get("reranker_enabled") or raw.get("reranker_ready"))
        ),
        "indexed_books": int(raw.get("indexed_books") or 0),
        "total_books": int(raw.get("total_books") or 0),
        "not_indexed_books": int(raw.get("not_indexed_books") or 0),
        "total_chunks": int(raw.get("total_chunks") or 0),
        "failed_jobs": int(raw.get("failed_jobs") or 0),
        "last_sync_at": raw.get("last_sync_at"),
        "model": raw.get("model"),
        "model_runtime": raw.get("model_runtime"),
        "reranker_ready": bool(raw.get("reranker_ready")),
        "reranker_model": raw.get("reranker_model"),
        "statuses": {
            key: {
                "books": int((value or {}).get("books") or 0),
                "chunks": int((value or {}).get("chunks") or 0),
            }
            for key, value in statuses.items()
            if isinstance(value, dict)
        },
        "activity": {
            **{
                key: raw_activity.get(key, 0)
                for key in (
                    "ocr_running", "ocr_queued", "rag_running", "rag_queued",
                    "jobs_remaining", "total_remaining", "vector_sync_pending", "busy",
                )
            },
            "active_jobs": active_jobs,
        },
        "ocr": {
            "enabled": bool((raw.get("ocr") or {}).get("enabled")),
            "available": bool((raw.get("ocr") or {}).get("available")),
            "max_pages": int((raw.get("ocr") or {}).get("max_pages") or 0),
            "max_pages_source": (raw.get("ocr") or {}).get("max_pages_source"),
            "supported_formats": (raw.get("ocr") or {}).get("supported_formats") or [],
            "adapters": (raw.get("ocr") or {}).get("adapters") or {},
        },
    })


@api_v1.route("/rag/ocr-config", methods=["GET", "POST"])
def rag_ocr_config():
    guard = _rag_edit_guard()
    if guard:
        return guard
    try:
        if request.method == "GET":
            return jsonify(get_rag_ocr_config(_user_name()))
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _err("invalid_request", "Request body must be an object", 400)
        value = int(body.get("ocr_max_pages"))
        if not 1 <= value <= 10000:
            raise ValueError
        return jsonify(update_rag_ocr_config(_user_name(), value))
    except (TypeError, ValueError):
        return _err(
            "invalid_ocr_max_pages",
            "ocr_max_pages must be an integer between 1 and 10000",
            400,
        )
    except CalibreMCPClientError as exc:
        return _err("rag_backend_error", str(exc), exc.status_code)


@api_v1.route("/rag/search", methods=["POST"])
@limiter.limit(
    "20/minute",
    key_func=lambda: "rag-user:" + str(getattr(current_user, "id", "anonymous")),
)
def rag_search():
    guard = _guard()
    if guard:
        return guard
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("invalid_request", "Request body must be an object", 400)
    query = str(body.get("query") or "").strip()
    if len(query) < 2:
        return _err("invalid_query", "Query must contain at least 2 characters", 400)
    if len(query) > 1000:
        return _err("invalid_query", "Query is too long", 400)

    mode = str(body.get("mode") or "hybrid").strip().lower()
    if mode not in {"hybrid", "semantic", "lexical"}:
        return _err("invalid_mode", "Mode must be hybrid, semantic or lexical", 400)
    try:
        limit = max(1, min(30, int(body.get("limit") or 12)))
        max_per_book = max(1, min(10, int(body.get("max_chunks_per_book") or 3)))
        visible_ids = _visible_book_ids()
        requested_ids = _book_ids(body.get("book_ids"))
        scoped_ids = sorted(visible_ids.intersection(requested_ids)) if requested_ids is not None else sorted(visible_ids)
        if not scoped_ids:
            return jsonify({
                "success": True,
                "query": query,
                "query_terms": [],
                "mode": mode,
                "count": 0,
                "duration_ms": 0,
                "model": None,
                "results": [],
            })
        payload = {
            "query": query,
            "mode": mode,
            "limit": limit,
            "book_ids": scoped_ids,
            "authors": _string_list(body.get("authors")),
            "tags": _string_list(body.get("tags")),
            "formats": _string_list(body.get("formats")),
            "include_adjacent": bool(body.get("include_adjacent", True)),
            "max_chunks_per_book": max_per_book,
        }
    except (TypeError, ValueError) as exc:
        return _err("invalid_filter", str(exc), 400)
    if body.get("min_score") is not None:
        try:
            payload["min_score"] = float(body["min_score"])
        except (TypeError, ValueError):
            return _err("invalid_filter", "min_score must be numeric", 400)

    try:
        raw = search_rag(_user_name(), payload)
    except CalibreMCPClientError as exc:
        return _err("rag_backend_error", str(exc), exc.status_code)
    if not raw.get("success", True):
        return _err(
            "rag_search_failed",
            str(raw.get("error") or "RAG search failed"),
            503,
        )
    results = raw.get("results") if isinstance(raw.get("results"), list) else []
    results = [
        item for item in results
        if isinstance(item, dict) and int(item.get("book_id") or 0) in visible_ids
    ]
    return jsonify({
        "success": True,
        "query": raw.get("query") or query,
        "query_terms": raw.get("query_terms") or [],
        "query_expansions": raw.get("query_expansions") or {},
        "mode": raw.get("mode") or mode,
        "count": len(results),
        "duration_ms": raw.get("duration_ms"),
        "model": raw.get("model"),
        "reranker_backend": raw.get("reranker_backend"),
        "reranker_model": raw.get("reranker_model"),
        "results": [
            _result_item(item) for item in results if isinstance(item, dict)
        ],
    })


@api_v1.route("/books/<int:book_id>/ocr", methods=["POST"])
@limiter.limit(
    "10/minute",
    key_func=lambda: "ocr-user:" + str(getattr(current_user, "id", "anonymous")),
)
def book_ocr_start(book_id: int):
    guard = _ocr_guard(book_id)
    if guard:
        return guard
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return _err("invalid_request", "Request body must be an object", 400)
    raw_max_pages = body.get("max_pages")
    try:
        max_pages = None if raw_max_pages is None else max(1, int(raw_max_pages))
    except (TypeError, ValueError):
        return _err("invalid_max_pages", "max_pages must be a positive integer", 400)
    try:
        raw = queue_book_ocr(
            _user_name(),
            book_id,
            force=bool(body.get("force", False)),
            max_pages=max_pages,
        )
    except CalibreMCPClientError as exc:
        return _err("ocr_backend_error", str(exc), exc.status_code)
    payload = _ocr_response(raw)
    return jsonify(payload), 202 if payload.get("accepted") else 200


@api_v1.route("/books/<int:book_id>/ocr", methods=["GET"])
def book_ocr_status(book_id: int):
    guard = _ocr_guard(book_id)
    if guard:
        return guard
    raw_job_id = request.args.get("job_id")
    try:
        job_id = None if raw_job_id in {None, ""} else int(raw_job_id)
    except (TypeError, ValueError):
        return _err("invalid_job_id", "job_id must be an integer", 400)
    if job_id is not None and job_id <= 0:
        return _err("invalid_job_id", "job_id must be positive", 400)
    try:
        raw = get_book_ocr_status(_user_name(), book_id, job_id=job_id)
    except CalibreMCPClientError as exc:
        if exc.status_code == 404 and job_id is None:
            return jsonify(None)
        return _err("ocr_backend_error", str(exc), exc.status_code)
    return jsonify(_ocr_response(raw))
