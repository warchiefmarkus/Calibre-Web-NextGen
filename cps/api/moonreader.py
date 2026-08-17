# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user Moon+ Reader WebDAV settings and sync controls."""
from datetime import datetime, timezone

from flask import jsonify, request

from . import api_v1
from .. import calibre_db, logger, ub
from ..cw_login import current_user
from ..services.moonreader_webdav import (
    MoonReaderError,
    decrypt_password,
    encrypt_password,
    find_cache_locations,
    get_or_create_settings,
    normalize_base_url,
    normalize_cache_path,
    serialize_settings,
    test_connection,
)
from ..tasks.moonreader_sync import (
    moonreader_sync_pending, queue_moonreader_book_sync, queue_moonreader_sync,
)

log = logger.create()


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _guard():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    return None


def _row():
    row = get_or_create_settings(int(current_user.id))
    if row.sync_status in {"queued", "running"} and not moonreader_sync_pending(current_user.id):
        row.sync_status = "idle"
        row.last_sync_error = "The previous sync was interrupted by a server restart."
        ub.session.commit()
    return row


def _json_settings(row, **extra):
    payload = serialize_settings(row)
    payload.update(extra)
    return jsonify(payload)


def _text(value, field, maximum, *, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise MoonReaderError(f"{field} must be text.", code="invalid_settings")
    value = value.strip()
    if required and not value:
        raise MoonReaderError(f"{field} is required.", code="invalid_settings")
    if len(value) > maximum:
        raise MoonReaderError(f"{field} is too long.", code="invalid_settings")
    return value


@api_v1.route("/account/moonreader")
def get_moonreader_settings():
    guard = _guard()
    if guard:
        return guard
    return _json_settings(_row())


@api_v1.route("/account/moonreader", methods=["POST"])
def save_moonreader_settings():
    guard = _guard()
    if guard:
        return guard
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("invalid_settings", "Moon+ Reader settings must be an object.", 400)
    row = _row()
    try:
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise MoonReaderError("Enabled must be true or false.", code="invalid_settings")
            row.enabled = payload["enabled"]
        if "base_url" in payload:
            row.base_url = normalize_base_url(payload["base_url"])
        if "username" in payload:
            row.username = _text(payload["username"], "Username", 255, required=True)
        if "cache_path" in payload:
            row.cache_path = normalize_cache_path(payload["cache_path"])
        if payload.get("clear_password") is True:
            row.password_encrypted = None
        elif "password" in payload and str(payload.get("password") or ""):
            row.password_encrypted = encrypt_password(str(payload["password"]))
        ub.session.commit()
    except MoonReaderError as exc:
        ub.session.rollback()
        return _err(exc.code, str(exc), exc.status)
    except Exception:
        ub.session.rollback()
        log.exception("Could not save Moon+ Reader WebDAV settings")
        return _err("save_failed", "Could not save Moon+ Reader settings.", 500)
    return _json_settings(row)


@api_v1.route("/account/moonreader/test", methods=["POST"])
def test_moonreader_connection():
    guard = _guard()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return _err("invalid_settings", "Connection settings must be an object.", 400)
    row = _row()
    try:
        base_url = normalize_base_url(payload.get("base_url", row.base_url))
        username = _text(payload.get("username", row.username), "Username", 255, required=True)
        cache_path = normalize_cache_path(payload.get("cache_path", row.cache_path))
        password = str(payload.get("password") or "") or decrypt_password(row.password_encrypted)
        result = test_connection(
            base_url=base_url, username=username,
            password=password, cache_path=cache_path,
        )
        row.last_test_at = datetime.now(timezone.utc)
        row.last_test_status = "success"
        row.last_test_error = None
        ub.session.commit()
        return _json_settings(row, test=result)
    except MoonReaderError as exc:
        ub.session.rollback()
        try:
            row.last_test_at = datetime.now(timezone.utc)
            row.last_test_status = "error"
            row.last_test_error = str(exc)[:1024]
            ub.session.commit()
        except Exception:
            ub.session.rollback()
        return _err(exc.code, str(exc), exc.status)
    except Exception:
        ub.session.rollback()
        log.exception("Moon+ Reader WebDAV connection test failed")
        return _err("connection_failed", "Could not test the WebDAV connection.", 502)


@api_v1.route("/account/moonreader/discover", methods=["POST"])
def discover_moonreader_caches():
    guard = _guard()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return _err("invalid_settings", "Connection settings must be an object.", 400)
    row = _row()
    try:
        base_url = normalize_base_url(payload.get("base_url", row.base_url))
        username = _text(payload.get("username", row.username), "Username", 255, required=True)
        password = str(payload.get("password") or "") or decrypt_password(row.password_encrypted)
        return jsonify(find_cache_locations(
            base_url=base_url, username=username, password=password,
        ))
    except MoonReaderError as exc:
        return _err(exc.code, str(exc), exc.status)
    except Exception:
        log.exception("Moon+ Reader cache discovery failed")
        return _err("discovery_failed", "Could not search the WebDAV server for Moon+ sync folders.", 502)


@api_v1.route("/books/<int:book_id>/moonreader/sync")
def get_book_moonreader_sync_status(book_id):
    """Return whether bidirectional Moon+/Calibre reconciliation is still queued/running."""
    guard = _guard()
    if guard:
        return guard
    if not calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True
    ):
        return _err("not_found", "Book not found", 404)
    return jsonify({
        "book_id": int(book_id),
        "pending": moonreader_sync_pending(int(current_user.id), int(book_id)),
    })


@api_v1.route("/books/<int:book_id>/moonreader/sync", methods=["POST"])
def start_book_moonreader_sync(book_id):
    """Queue bidirectional Moon+/Calibre reconciliation for one visible book."""
    guard = _guard()
    if guard:
        return guard
    if not calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True
    ):
        return _err("not_found", "Book not found", 404)
    row = _row()
    if not row.enabled:
        return _err("sync_disabled", "Enable Moon+ Reader sync first.", 400)
    if not row.password_encrypted:
        return _err("password_required", "Configure the WebDAV password first.", 400)
    if not row.cache_path:
        return _err(
            "cache_path_required",
            "Find and select a Moon+ sync folder before synchronizing.",
            400,
        )
    try:
        result = queue_moonreader_book_sync(
            int(current_user.id), int(book_id), None,
            username=current_user.name or "System",
        )
    except Exception:
        ub.session.rollback()
        log.exception("Could not queue Moon+ Reader sync for book %s", book_id)
        return _err("queue_failed", "Could not queue Moon+ Reader sync for this book.", 500)
    return jsonify({
        "book_id": int(book_id),
        "queued": bool(result.get("queued")),
        "pending": bool(result.get("pending")),
    }), 202


@api_v1.route("/account/moonreader/sync", methods=["POST"])
def start_moonreader_sync():
    guard = _guard()
    if guard:
        return guard
    row = _row()
    if not row.enabled:
        return _err("sync_disabled", "Enable Moon+ Reader sync first.", 400)
    if not row.password_encrypted:
        return _err("password_required", "Configure the WebDAV password first.", 400)
    if not row.cache_path:
        return _err(
            "cache_path_required",
            "Find and select a Moon+ sync folder before synchronizing.",
            400,
        )
    try:
        result = queue_moonreader_sync(int(current_user.id), current_user.name)
    except Exception:
        ub.session.rollback()
        log.exception("Could not queue Moon+ Reader sync")
        return _err("queue_failed", "Could not queue Moon+ Reader sync.", 500)
    row = _row()
    return _json_settings(row, queued=bool(result.get("queued") or result.get("pending"))), 202
