# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user reading-progress summaries for catalog and book-detail APIs."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os

from sqlalchemy import bindparam, text

from .. import calibre_db, deployment_profile, logger, ub

log = logger.create()


def _aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _candidate(percentage, updated_at, source):
    try:
        value = float(percentage)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0 or value > 100:
        return None
    timestamp = _aware(updated_at)
    return {
        "percentage": value,
        "updated_at": timestamp.isoformat() if timestamp else None,
        "source": source,
        "_timestamp": timestamp,
    }


def _newer(current, candidate):
    if candidate is None:
        return current
    if current is None:
        return candidate

    # A zero native position is commonly emitted when a reader is merely opened
    # at the beginning. Treat it as an initialization marker when another source
    # already has real progress, otherwise it would erase a valid Moon+ position.
    current_value = float(current.get("percentage", 0))
    candidate_value = float(candidate.get("percentage", 0))
    if current_value == 0 < candidate_value:
        return candidate
    if candidate_value == 0 < current_value:
        return current

    left = current.get("_timestamp")
    right = candidate.get("_timestamp")
    if left is None:
        return candidate
    if right is None:
        return current
    # Equal timestamps prefer the canonical database carrier.
    if right > left or (right == left and candidate.get("source") == "calibre_web"):
        return candidate
    return current


def _native_reader_username(user_name):
    explicit = os.environ.get("CWNG_NATIVE_READER_USERNAME", "").strip()
    if explicit:
        return explicit
    name = str(user_name or "").strip()
    if not name:
        return ""
    template = os.environ.get("CWNG_NATIVE_READER_USERNAME_TEMPLATE", "cwng-{user}")
    try:
        return template.format(user=name)
    except (KeyError, ValueError):
        log.warning("Invalid CWNG_NATIVE_READER_USERNAME_TEMPLATE; using cwng-{user}")
        return f"cwng-{name}"


def _native_progress(session, user_name, ids):
    native_user = _native_reader_username(user_name)
    if not native_user:
        return {}
    statement = text(
        "SELECT book, pos_frac, epoch FROM last_read_positions "
        "WHERE user = :reader_user AND book IN :book_ids"
    ).bindparams(bindparam("book_ids", expanding=True))
    selected = {}
    for row in session.execute(statement, {
            "reader_user": native_user, "book_ids": tuple(ids)}):
        try:
            updated_at = datetime.fromtimestamp(float(row.epoch), tz=timezone.utc)
            percentage = float(row.pos_frac) * 100.0
            book_id = int(row.book)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        candidate = _candidate(percentage, updated_at, "calibre_web")
        selected[book_id] = _newer(selected.get(book_id), candidate)
    return selected


def _legacy_progress(session, user_id, ids):
    selected = {}
    rows = (session.query(
        ub.KoboReadingState.book_id.label("book_id"),
        ub.KoboBookmark.progress_percent.label("percentage"),
        ub.KoboBookmark.last_modified.label("bookmark_modified"),
        ub.KoboReadingState.last_modified.label("state_modified"),
    ).join(
        ub.KoboBookmark,
        ub.KoboBookmark.kobo_reading_state_id == ub.KoboReadingState.id,
    ).filter(
        ub.KoboReadingState.user_id == user_id,
        ub.KoboReadingState.book_id.in_(ids),
        ub.KoboBookmark.progress_percent.isnot(None),
    ).all())
    for row in rows:
        book_id = int(row.book_id)
        candidate = _candidate(
            row.percentage,
            row.bookmark_modified or row.state_modified,
            "calibre_web",
        )
        selected[book_id] = _newer(selected.get(book_id), candidate)
    return selected


def reading_progress_summary_map(
        session, user_id, book_ids, *, user_name=None, native_session=None):
    """Return the newest progress carrier for each requested book.

    In the MCP-managed profile, Calibre's native ``last_read_positions`` table
    is the database source of truth. Moon+ contributes the WebDAV ``.po`` file
    modification time. Standard deployments retain the legacy app.db bookmark
    fallback. Both paths use one query per source for the whole catalog page.
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {}
    ids = []
    for value in book_ids or []:
        try:
            book_id = int(value)
        except (TypeError, ValueError):
            continue
        if book_id > 0 and book_id not in ids:
            ids.append(book_id)
    if not ids:
        return {}

    selected = {}
    try:
        if deployment_profile.use_calibre_native_reader_data():
            selected = _native_progress(
                native_session if native_session is not None else calibre_db.session,
                user_name, ids)
        else:
            selected = _legacy_progress(session, uid, ids)
    except Exception:
        log.warning("Database reading-progress summaries unavailable", exc_info=True)

    try:
        moon_rows = (session.query(ub.MoonReaderProgress)
                     .filter(ub.MoonReaderProgress.user_id == uid,
                             ub.MoonReaderProgress.book_id.in_(ids)).all())
        for row in moon_rows:
            book_id = int(row.book_id)
            # In the MCP-managed profile reconciliation mirrors Moon into
            # last_read_positions. Once a native row exists it is the canonical
            # carrier used by the reader and every progress badge.
            if (deployment_profile.use_calibre_native_reader_data() and
                    book_id in selected and
                    float(selected[book_id].get("percentage") or 0) > 0):
                continue
            candidate = _candidate(
                row.percentage,
                row.remote_modified or row.synced_at or row.moon_timestamp,
                "moonreader",
            )
            selected[book_id] = _newer(selected.get(book_id), candidate)
    except Exception:
        log.warning("Moon+ reading-progress summaries unavailable", exc_info=True)

    for summary in selected.values():
        summary.pop("_timestamp", None)
    return selected
