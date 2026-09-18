# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader progress (bookmark) endpoints for /api/v1.

In the standard profile this reads/writes the legacy ``ub.Bookmark`` row.
In ``mcp-managed-library`` it preserves the same SPA contract while storing the
CFI and normalized progress in Calibre's native ``last_read_positions`` table
through the private CalibreMCP REST adapter.
"""
from datetime import datetime, timezone
from pathlib import Path
import uuid

from flask import g, jsonify, request
from sqlalchemy import and_
from sqlalchemy.orm.attributes import flag_modified

from . import api_v1
from .. import calibre_db, config, deployment_profile, logger, ub
from ..cw_login import current_user
from ..services import reading_position, reading_sources, storyteller_source
from ..services.calibremcp_client import (
    CalibreMCPClientError,
    get_reader_position,
    set_reader_position,
)
from ..usermanagement import login_required_if_no_ano
from ..reader_settings import merged_reader_settings, resolved_reader_settings
from ..services.reading_progress import reading_progress_summary_map


log = logger.create()


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


def _can_browse_global():
    """Mirror the book-detail role gate without weakening content filters."""
    try:
        return bool(current_user.role_browse_global())
    except (AttributeError, RuntimeError):
        return False


def _bookmark_filter(book_id, fmt):
    return and_(
        ub.Bookmark.user_id == int(current_user.id),
        ub.Bookmark.book_id == book_id,
        ub.Bookmark.format == fmt,
    )


def _cwng_user_name():
    return str(getattr(current_user, "name", "") or "").strip()


def _latest_native_position(payload):
    positions = payload.get("positions", []) if isinstance(payload, dict) else []
    if not positions:
        return None
    return max(positions, key=lambda item: float(item.get("epoch") or 0))


@api_v1.route("/books/<int:book_id>/bookmark")
@login_required_if_no_ano
def get_bookmark(book_id):
    """Return the saved reading position (epub.js CFI) for this user/book/format."""
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    fmt = (request.args.get("format") or "epub").lower()
    if deployment_profile.use_calibre_native_reader_data():
        native = None
        native_error = None
        try:
            native = _latest_native_position(
                get_reader_position(_cwng_user_name(), book_id, fmt)
            )
        except CalibreMCPClientError as exc:
            native_error = exc

        summary = reading_progress_summary_map(
            ub.session, int(current_user.id), [book_id],
            user_name=_cwng_user_name(),
        ).get(book_id)
        native_device = str((native or {}).get("device") or "")
        moon_origin = native_device.startswith("moonreader-webdav:")
        if moon_origin or (summary and summary.get("source") == "moonreader"):
            anchor = None
            try:
                from ..services.moonreader_webdav import moon_position_anchor
                anchor = moon_position_anchor(int(current_user.id), book_id, fmt)
            except Exception:
                log.warning(
                    "Could not resolve Moon+ restore anchor for book %s",
                    book_id, exc_info=True,
                )
            native_fraction = float((native or {}).get("pos_frac") or 0)
            summary_fraction = (
                float(summary.get("percentage") or 0) / 100.0 if summary else 0.0
            )
            fraction = native_fraction if native_fraction > 0 else summary_fraction
            return jsonify({
                "bookmark": native.get("cfi") if native and fmt == "pdf" else None,
                "resume": None,
                "position_fraction": fraction,
                "position_source": "moonreader",
                "position_anchor": anchor.get("text") if anchor else None,
                "position_chapter": anchor.get("chapter") if anchor else None,
                "position_section": anchor.get("foliate_section") if anchor else None,
                "position_percentage": (
                    anchor.get("percentage") if anchor else
                    float(summary.get("percentage") or 0) if summary else
                    fraction * 100.0
                ),
            })
        if native_error is not None:
            return _err("reader_backend_error", str(native_error), native_error.status_code)
        return jsonify({
            "bookmark": native.get("cfi") if native else None,
            "resume": None,
            "position_fraction": float(native.get("pos_frac") or 0) if native else 0,
            "position_source": "calibre_web" if native else None,
        })

    return jsonify(reading_position.read_resume_position(
        ub.session.get_bind(), int(current_user.id), book_id, fmt,
    ))


@api_v1.route("/books/<int:book_id>/bookmark", methods=["POST"])
@login_required_if_no_ano
def save_bookmark(book_id):
    """Persist (or, with an empty bookmark, clear) the reading position. Mirrors
    the legacy set_bookmark write so the two readers share one row."""
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    try:
        from ..services.device_registry import (
            WEBREADER_INSTALLATION_ID_HEADER,
            ensure_webreader_device_best_effort,
        )
        g.annotation_origin_device_id = ensure_webreader_device_best_effort(
            user_id=current_user.id,
            installation_id=request.headers.get(WEBREADER_INSTALLATION_ID_HEADER),
        )
    except Exception:
        log.warning("Best-effort web-reader device observation failed", exc_info=True)
        g.annotation_origin_device_id = None
    data = request.get_json(silent=True) or {}
    fmt = (data.get("format") or "epub").lower()
    bookmark_key = data.get("bookmark") or ""
    if deployment_profile.use_calibre_native_reader_data():
        try:
            raw_fraction = data.get("position_fraction")
            if raw_fraction is None:
                percentage = reading_position.coerce_percentage(data.get("percentage"))
                raw_fraction = (percentage / 100.0) if percentage is not None else 0
            renderer_fraction = max(0.0, min(1.0, float(raw_fraction or 0)))
            canonical_fraction = renderer_fraction
            anchor_text = data.get("position_anchor")
            try:
                from ..services.moonreader_webdav import canonical_fraction_from_anchor
                canonical_fraction = canonical_fraction_from_anchor(
                    int(current_user.id), book_id, fmt, renderer_fraction, anchor_text,
                )
            except Exception:
                # Exact CFI persistence must still work when text canonicalization
                # is unavailable. The raw renderer fraction is only a fallback.
                log.exception("Could not canonicalize reader progress for book %s", book_id)
            set_reader_position(
                _cwng_user_name(),
                book_id,
                fmt,
                cfi=bookmark_key or None,
                position_fraction=canonical_fraction,
                device=str(data.get("device") or "cwng-web"),
            )
        except (CalibreMCPClientError, TypeError, ValueError) as exc:
            status = exc.status_code if isinstance(exc, CalibreMCPClientError) else 400
            return _err("reader_backend_error", str(exc), status)
        if data.get("share_with_devices", True) is not False:
            try:
                from ..tasks.moonreader_sync import queue_moonreader_book_sync
                queue_moonreader_book_sync(
                    int(current_user.id), book_id, fmt,
                    anchor_text=anchor_text,
                    username=_cwng_user_name() or "System",
                )
            except Exception:
                # Reader position persistence is authoritative and must not fail just
                # because the optional WebDAV bridge is temporarily unavailable.
                log.exception("Could not queue Moon+ writeback for book %s", book_id)
        return jsonify({
            "position_fraction": canonical_fraction,
            "renderer_fraction": renderer_fraction,
        })

    # Replace-on-write: one bookmark per (user, book, format), like the legacy route.
    ub.session.query(ub.Bookmark).filter(_bookmark_filter(book_id, fmt)).delete()
    if bookmark_key:
        row = ub.session.merge(ub.Bookmark(
            user_id=current_user.id,
            book_id=book_id,
            format=fmt,
            bookmark_key=bookmark_key,
        ))
        # #1318: settle the required write before the optional one, so a bookmark
        # failure is not reported in the vocabulary of a progress-sharing failure
        # (and so the savepoint below cannot roll the bookmark back with it).
        if not ub.session_flush():
            return "", 500

        # #324: share the portable half of the position (the percentage) with the
        # user's other devices. Mirrors the legacy route so both readers behave
        # the same. Only on a save — an empty bookmark is a clear.
        percentage = reading_position.coerce_percentage(data.get("percentage"))
        if percentage is not None:
            try:
                reading_position.record_web_reader_progress(
                    current_user,
                    book_id,
                    percentage,
                    origin_device_id=g.annotation_origin_device_id,
                    cfi=bookmark_key,
                    share_with_devices=data.get("share_with_devices", True) is not False,
                )
            except Exception as e:
                # Position sharing must never cost the user their bookmark.
                log.warning("Could not share web reader progress for book %s: %s", book_id, e)

        # Stamp after sharing: our own mirror must never supersede this CFI.
        row.updated_at = datetime.now(timezone.utc)

    # The SPA debounces one of these every 800ms; answering 204 on a rolled-back
    # write drops the position silently and tells the client not to retry.
    if not ub.session_commit("Bookmark for user {} in book {} via api".format(current_user.id, book_id)):
        return "", 500
    return "", 204


def _reader_bookmark_dict(row):
    return {
        "bookmark_id": row.bookmark_id,
        "book_id": row.book_id,
        "format": row.format,
        "locator": row.locator,
        "progression": float(row.progression or 0),
        "label": row.label,
        "chapter": row.chapter,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _reader_book_state_format(value):
    return str(value or "epub").strip().lower()[:16] or "epub"


def _reader_book_state_query(book_id, fmt):
    return ub.session.query(ub.ReaderBookState).filter(
        ub.ReaderBookState.user_id == int(current_user.id),
        ub.ReaderBookState.book_id == book_id,
        ub.ReaderBookState.format == fmt,
    )


def _reader_book_state_dict(row, book_id, fmt):
    return {
        "book_id": book_id,
        "format": fmt,
        "translationEnabled": bool(row.translation_enabled) if row else False,
        "translationView": str(row.translation_view or "original") if row else "original",
    }


@api_v1.route("/books/<int:book_id>/reader-state")
@login_required_if_no_ano
def get_reader_book_state(book_id):
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    fmt = _reader_book_state_format(request.args.get("format"))
    row = _reader_book_state_query(book_id, fmt).first()
    return jsonify(_reader_book_state_dict(row, book_id, fmt))


@api_v1.route("/books/<int:book_id>/reader-state", methods=["POST"])
@login_required_if_no_ano
def save_reader_book_state(book_id):
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err("invalid_reader_state", "Reader book state must be an object", 400)
    if "translationEnabled" in data and not isinstance(data["translationEnabled"], bool):
        return _err("invalid_reader_state", "translationEnabled must be a boolean", 400)
    if "translationView" in data and data["translationView"] not in {"original", "translated"}:
        return _err("invalid_reader_state", "Invalid translationView", 400)
    fmt = _reader_book_state_format(data.get("format"))
    row = _reader_book_state_query(book_id, fmt).first()
    if row is None:
        row = ub.ReaderBookState(
            user_id=int(current_user.id), book_id=book_id, format=fmt,
            translation_enabled=False, translation_view="original",
        )
        ub.session.add(row)

    if "translationEnabled" in data:
        row.translation_enabled = data["translationEnabled"]
        if not row.translation_enabled and "translationView" not in data:
            row.translation_view = "original"
    if "translationView" in data:
        row.translation_view = data["translationView"]

    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        return _err("save_failed", "Could not save reader book state", 500)
    return jsonify(_reader_book_state_dict(row, book_id, fmt))


def _reader_bookmark_query(book_id):
    return ub.session.query(ub.ReaderBookmark).filter(
        ub.ReaderBookmark.user_id == int(current_user.id),
        ub.ReaderBookmark.book_id == book_id,
    )


@api_v1.route("/books/<int:book_id>/reader-bookmarks")
@login_required_if_no_ano
def get_reader_bookmarks(book_id):
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    fmt = (request.args.get("format") or "").strip().lower()
    query = _reader_bookmark_query(book_id)
    if fmt:
        query = query.filter(ub.ReaderBookmark.format == fmt)
    rows = query.order_by(
        ub.ReaderBookmark.progression.asc(),
        ub.ReaderBookmark.created_at.asc(),
    ).all()
    return jsonify({"bookmarks": [_reader_bookmark_dict(row) for row in rows]})


@api_v1.route("/books/<int:book_id>/reader-bookmarks", methods=["POST"])
@login_required_if_no_ano
def create_reader_bookmark(book_id):
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    data = request.get_json(silent=True) or {}
    locator = str(data.get("locator") or "").strip()
    fmt = str(data.get("format") or "epub").strip().lower()[:16]
    if not locator or len(locator) > 8192:
        return _err("invalid_locator", "A valid reader locator is required", 400)
    try:
        progression = max(0.0, min(1.0, float(data.get("progression") or 0)))
    except (TypeError, ValueError):
        return _err("invalid_progression", "Progression must be a number", 400)
    row = ub.ReaderBookmark(
        bookmark_id="cwn-reader-" + uuid.uuid4().hex,
        user_id=int(current_user.id),
        book_id=book_id,
        format=fmt,
        locator=locator,
        progression=progression,
        label=str(data.get("label") or "").strip()[:500] or None,
        chapter=str(data.get("chapter") or "").strip()[:500] or None,
    )
    ub.session.add(row)
    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        return _err("save_failed", "Could not save reader bookmark", 500)
    return jsonify(_reader_bookmark_dict(row)), 201


@api_v1.route(
    "/books/<int:book_id>/reader-bookmarks/<bookmark_id>", methods=["DELETE"]
)
@login_required_if_no_ano
def delete_reader_bookmark(book_id, bookmark_id):
    guard = _require_real_user()
    if guard:
        return guard
    visible = _require_visible_book(book_id)
    if visible:
        return visible
    row = _reader_bookmark_query(book_id).filter(
        ub.ReaderBookmark.bookmark_id == bookmark_id,
    ).first()
    if row is None:
        return _err("not_found", "Reader bookmark not found", 404)
    ub.session.delete(row)
    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        return _err("delete_failed", "Could not delete reader bookmark", 500)
    return "", 204


def _book_epub_path(book):
    """Return the visible book's contained EPUB path, or ``None``."""
    data_rows = getattr(book, "data", None) or ()
    if not any(str(data.format).lower() == "epub" for data in data_rows):
        return None
    root = Path(config.get_book_path()).resolve()
    for data in data_rows:
        if str(data.format).lower() != "epub":
            continue
        path = (root / book.path / (data.name + ".epub")).resolve()
        if path.is_relative_to(root) and path.is_file():
            return path
    return None


@api_v1.route("/books/<int:book_id>/reading-sources")
@login_required_if_no_ano
def get_reading_sources(book_id):
    """Return selectable, attributed positions for one visible EPUB.

    Device rows are last reports. The resolved Kobo bookmark is intentionally a
    separate source because its storage table cannot prove which device caused
    the winning value. External connectors are read-only and best effort.
    """
    guard = _require_real_user()
    if guard:
        return guard
    # Match the authorized reader/book-detail surface: hidden and archived are
    # listing states, while a curator may deep-link into the global catalogue.
    # common_filters() still enforces language/content/role restrictions.
    book = calibre_db.get_filtered_book(
        book_id,
        allow_show_archived=True,
        allow_show_hidden=True,
        allow_show_global=_can_browse_global(),
    )
    if book is None:
        return _err("not_found", "Book not found", 404)

    user_id = int(current_user.id)
    devices = (ub.session.query(ub.Device)
               .filter(ub.Device.user_id == user_id)
               .order_by(ub.Device.active.desc(), ub.Device.id)
               .all())
    positions = (ub.session.query(ub.DeviceReadingPosition)
                 .filter(
                     ub.DeviceReadingPosition.device_id.in_([row.id for row in devices]),
                     ub.DeviceReadingPosition.book_id == book_id,
                 ).all()) if devices else []
    epub_path = _book_epub_path(book)
    sources = reading_sources.device_source_rows(
        devices, positions, book_id=book_id, epub_path=epub_path,
    )

    integration = {"configured": False, "reachable": None}
    client = storyteller_source.configured_client(user_id)
    if client is not None:
        integration["configured"] = True
        if epub_path is not None:
            try:
                source = storyteller_source.read_source(
                    client,
                    title=book.title,
                    authors=[author.name for author in getattr(book, "authors", ())],
                    epub_path=epub_path,
                )
                integration["reachable"] = True
                if source is not None:
                    sources.append(source)
            except Exception:
                integration["reachable"] = False
                log.warning(
                    "Could not read configured Storyteller position for user %s book %s",
                    user_id, book_id, exc_info=True,
                )

    state = (ub.session.query(ub.KoboReadingState)
             .filter_by(user_id=user_id, book_id=book_id).first())
    resolved = reading_sources.resolved_source_row(
        state.current_bookmark if state else None, book_id=book_id,
    )
    if resolved is not None:
        sources.append(resolved)
    return jsonify({
        "book_id": book_id,
        "sources": sources,
        "integrations": {"storyteller": integration},
    })


@api_v1.route("/reader/settings")
@login_required_if_no_ano
def get_reader_settings():
    """Return the complete per-user appearance contract shared by both readers."""
    guard = _require_real_user()
    if guard:
        return guard
    current = (getattr(current_user, "view_settings", None) or {}).get("reader", {})
    resolved = resolved_reader_settings(current)
    # These two values are per-book. Force safe legacy defaults here so even a
    # stale cached frontend cannot inherit auto-translation from another book.
    resolved["translationEnabled"] = False
    resolved["translationView"] = "original"
    return jsonify({"reader": resolved})


@api_v1.route("/reader/settings", methods=["POST"])
@login_required_if_no_ano
def save_reader_settings():
    """Merge a partial reader appearance update into User.view_settings."""
    guard = _require_real_user()
    if guard:
        return guard
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("invalid_settings", "Reader settings must be an object", 400)
    # Auto-translation is book-scoped. Ignore these legacy global fields so an
    # old cached frontend cannot make a choice in one book leak into another.
    payload = {
        key: value for key, value in payload.items()
        if key not in {"translationEnabled", "translationView"}
    }
    view_settings = dict(getattr(current_user, "view_settings", None) or {})
    current_reader = dict(view_settings.get("reader", {}) or {})
    current_reader.pop("translationEnabled", None)
    current_reader.pop("translationView", None)
    merged = merged_reader_settings(current_reader, payload)
    view_settings["reader"] = merged
    current_user.view_settings = view_settings
    flag_modified(current_user, "view_settings")
    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        return _err("save_failed", "Could not save reader settings", 500)
    return jsonify({"reader": resolved_reader_settings(merged)})
