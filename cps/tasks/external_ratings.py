# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background population of cached external book ratings."""

from flask_babel import lazy_gettext as N_
from threading import Lock

from cps import calibre_db, config, logger, ub
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED, WorkerThread

log = logger.create()

_pending_lock = Lock()
_pending_book_ids = set()


def _server_hardcover_tokens():
    token = str(config.resolved_hardcover_token() or "").replace("Bearer ", "", 1).strip()
    return [token] if token else []


def _server_google_books_api_key():
    return str(getattr(config, "config_google_books_api_key", None) or "").strip() or None


def _book_ids(values):
    result = []
    for raw in values or []:
        try:
            book_id = int(raw)
        except (TypeError, ValueError):
            continue
        if book_id > 0 and book_id not in result:
            result.append(book_id)
    return result


def queue_external_rating_refresh(
    book_ids,
    username="System",
    hardcover_tokens=None,
    google_books_api_key=None,
    force=False,
):
    parsed = _book_ids(book_ids)
    if not parsed:
        return {"success": True, "skipped": True, "reason": "no_book_ids"}

    with _pending_lock:
        pending = [book_id for book_id in parsed if book_id in _pending_book_ids]
        claimed = [book_id for book_id in parsed if book_id not in _pending_book_ids]
        _pending_book_ids.update(claimed)
    if not claimed:
        return {
            "success": True,
            "queued": False,
            "pending": bool(pending),
            "book_ids": parsed,
        }

    try:
        WorkerThread.add(
            username,
            TaskExternalRatings(
                claimed,
                hardcover_tokens=hardcover_tokens,
                google_books_api_key=google_books_api_key,
                force=force,
            ),
            hidden=True,
        )
    except Exception:
        with _pending_lock:
            _pending_book_ids.difference_update(claimed)
        raise
    return {"success": True, "queued": True, "book_ids": claimed}


def external_rating_refresh_pending(book_id):
    try:
        value = int(book_id)
    except (TypeError, ValueError):
        return False
    with _pending_lock:
        return value in _pending_book_ids


class TaskExternalRatings(CalibreTask):
    """Refresh one or more books without blocking their import request."""

    def __init__(self, book_ids, hardcover_tokens=None, google_books_api_key=None, force=False):
        super().__init__(N_("Loading external book ratings"))
        self.book_ids = _book_ids(book_ids)
        self.hardcover_tokens = hardcover_tokens
        self.google_books_api_key = google_books_api_key
        self.force = bool(force)
        self.self_cleanup = True

    @property
    def name(self):
        return str(N_("External book ratings"))

    @property
    def is_cancellable(self):
        return True

    def run(self, worker_thread):
        from cps.api.external_ratings import load_or_refresh_external_ratings

        try:
            try:
                ub.init_db_thread()
            except Exception:
                pass
            calibre_db.ensure_session()

            tokens = self.hardcover_tokens
            if tokens is None:
                tokens = _server_hardcover_tokens()
            google_key = self.google_books_api_key
            if google_key is None:
                google_key = _server_google_books_api_key()

            total = len(self.book_ids)
            for index, book_id in enumerate(self.book_ids, start=1):
                if self.stat in (STAT_CANCELLED, STAT_ENDED):
                    return
                self.message = N_(
                    "Loading external ratings: %(current)s/%(total)s",
                    current=index,
                    total=total,
                )
                try:
                    load_or_refresh_external_ratings(
                        book_id,
                        hardcover_tokens=tokens,
                        force=self.force,
                        google_books_api_key=google_key,
                        unfiltered=True,
                    )
                except Exception:
                    log.error(
                        "[external-ratings] Background refresh failed for book %s",
                        book_id,
                        exc_info=True,
                    )
                self.progress = index / total if total else 1

            self._handleSuccess()
        finally:
            try:
                calibre_db.session.rollback()
                calibre_db.session.expire_all()
            except Exception:
                pass
            try:
                ub.session.remove()
            except Exception:
                pass
            with _pending_lock:
                _pending_book_ids.difference_update(self.book_ids)
