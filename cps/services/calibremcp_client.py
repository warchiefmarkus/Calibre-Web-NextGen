# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private CalibreMCP REST client for the managed-library profile."""

from __future__ import annotations

import os
from typing import Any

import requests


class CalibreMCPClientError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def _base_url() -> str:
    return os.environ.get("CWNG_MCP_URL", "http://127.0.0.1:10720").rstrip("/")


def _headers(cwng_user: str) -> dict[str, str]:
    token = os.environ.get("CWNG_CALIBREMCP_REST_TOKEN", "").strip()
    if not token:
        raise CalibreMCPClientError("CalibreMCP REST token is not configured")
    return {
        "Authorization": f"Bearer {token}",
        "X-CWNG-User": cwng_user,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(
    method: str,
    path: str,
    cwng_user: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = _headers(cwng_user)
    if extra_headers:
        headers.update(extra_headers)
    try:
        response = requests.request(
            method,
            _base_url() + path,
            headers=headers,
            params=params,
            json=payload,
            timeout=(3.0, timeout_seconds),
        )
    except requests.RequestException as exc:
        raise CalibreMCPClientError("CalibreMCP service is unavailable") from exc

    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if response.status_code >= 400:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        message = error.get("message") or f"CalibreMCP returned HTTP {response.status_code}"
        status = response.status_code if response.status_code in {400, 401, 403, 404} else 503
        raise CalibreMCPClientError(message, status)
    if not isinstance(data, dict):
        raise CalibreMCPClientError("CalibreMCP returned an invalid response")
    return data


def update_book_metadata(
    cwng_user: str, book_id: int, metadata: dict[str, Any]
) -> dict[str, Any]:
    return _request(
        "PUT",
        f"/api/v1/books/{book_id}/metadata",
        cwng_user,
        payload={"metadata": metadata},
        timeout_seconds=60.0,
    )


def update_book_cover(
    cwng_user: str, book_id: int, staged_path: str
) -> dict[str, Any]:
    return _request(
        "PUT",
        f"/api/v1/books/{book_id}/cover",
        cwng_user,
        payload={"path": staged_path},
        timeout_seconds=90.0,
    )


def add_book_format(
    cwng_user: str, book_id: int, staged_path: str
) -> dict[str, Any]:
    return _request(
        "PUT",
        f"/api/v1/books/{book_id}/formats",
        cwng_user,
        payload={"path": staged_path, "replace": True},
        timeout_seconds=180.0,
    )


def delete_book_format(
    cwng_user: str, book_id: int, format_name: str
) -> dict[str, Any]:
    return _request(
        "DELETE",
        f"/api/v1/books/{book_id}/formats/{format_name}",
        cwng_user,
        timeout_seconds=180.0,
    )


def convert_book_format(
    cwng_user: str,
    book_id: int,
    source_format: str,
    target_format: str,
) -> dict[str, Any]:
    return _request(
        "POST",
        f"/api/v1/books/{book_id}/convert",
        cwng_user,
        payload={"from": source_format, "to": target_format},
        timeout_seconds=1800.0,
    )


def import_book(
    cwng_user: str, staged_path: str, original_filename: str,
    *, idempotency_key: str | None = None,
) -> dict[str, Any]:
    return _request(
        "POST",
        "/api/v1/books/import",
        cwng_user,
        payload={
            "path": staged_path,
            "original_filename": original_filename,
        },
        timeout_seconds=300.0,
        extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
    )


def delete_book(
    cwng_user: str, book_id: int
) -> dict[str, Any]:
    return _request(
        "DELETE",
        f"/api/v1/books/{book_id}",
        cwng_user,
        timeout_seconds=300.0,
    )


def get_rag_status(cwng_user: str) -> dict[str, Any]:
    return _request(
        "GET", "/api/v1/rag/status", cwng_user, timeout_seconds=20.0
    )


def search_rag(cwng_user: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _request(
        "POST",
        "/api/v1/rag/search",
        cwng_user,
        payload=payload,
        timeout_seconds=90.0,
    )


def get_rag_ocr_config(cwng_user: str) -> dict[str, Any]:
    return _request(
        "GET", "/api/v1/rag/ocr-config", cwng_user, timeout_seconds=20.0
    )


def update_rag_ocr_config(cwng_user: str, max_pages: int) -> dict[str, Any]:
    return _request(
        "POST",
        "/api/v1/rag/ocr-config",
        cwng_user,
        payload={"ocr_max_pages": int(max_pages)},
        timeout_seconds=20.0,
    )


def get_reader_position(cwng_user: str, book_id: int, fmt: str) -> dict[str, Any]:
    return _request(
        "GET",
        f"/api/v1/reader/books/{book_id}/position",
        cwng_user,
        params={"format": fmt.upper()},
    )


def set_reader_position(
    cwng_user: str,
    book_id: int,
    fmt: str,
    *,
    cfi: str | None,
    position_fraction: float,
    device: str = "cwng-web",
) -> dict[str, Any]:
    return _request(
        "PUT",
        f"/api/v1/reader/books/{book_id}/position",
        cwng_user,
        payload={
            "format": fmt.upper(),
            "device": device[:100] or "cwng-web",
            "cfi": cfi,
            "position_fraction": max(0.0, min(1.0, float(position_fraction))),
        },
    )


def get_reader_annotations(cwng_user: str, book_id: int, fmt: str) -> dict[str, Any]:
    return _request(
        "GET",
        f"/api/v1/reader/books/{book_id}/annotations",
        cwng_user,
        params={"format": fmt.upper()},
    )


def create_reader_annotation(
    cwng_user: str, book_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    return _request(
        "POST",
        f"/api/v1/reader/books/{book_id}/annotations",
        cwng_user,
        payload=payload,
    )


def update_reader_annotation(
    cwng_user: str,
    book_id: int,
    annotation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        "PATCH",
        f"/api/v1/reader/books/{book_id}/annotations/{annotation_id}",
        cwng_user,
        payload=payload,
    )


def delete_reader_annotation(
    cwng_user: str, book_id: int, annotation_id: str, fmt: str
) -> dict[str, Any]:
    return _request(
        "DELETE",
        f"/api/v1/reader/books/{book_id}/annotations/{annotation_id}",
        cwng_user,
        params={"format": fmt.upper()},
    )


def queue_book_ocr(
    cwng_user: str,
    book_id: int,
    *,
    force: bool = False,
    max_pages: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"force": bool(force)}
    if max_pages is not None:
        payload["max_pages"] = int(max_pages)
    return _request(
        "POST",
        f"/api/v1/books/{book_id}/ocr",
        cwng_user,
        payload=payload,
        timeout_seconds=20.0,
    )


def get_book_ocr_status(
    cwng_user: str,
    book_id: int,
    *,
    job_id: int | None = None,
) -> dict[str, Any]:
    params = {"job_id": int(job_id)} if job_id is not None else None
    return _request(
        "GET",
        f"/api/v1/books/{book_id}/ocr",
        cwng_user,
        params=params,
        timeout_seconds=20.0,
    )
