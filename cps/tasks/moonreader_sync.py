# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background bidirectional Moon+ Reader WebDAV reconciliation."""
from datetime import datetime, timezone
from threading import Lock

from flask_babel import lazy_gettext as N_

from cps import logger, ub
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED, WorkerThread

log = logger.create()
_pending_lock = Lock()
_pending_keys: set[tuple[int, int]] = set()
_poll_started = False


def _key(user_id, book_id=None) -> tuple[int, int]:
    return int(user_id), int(book_id or 0)


def moonreader_sync_pending(user_id, book_id=None) -> bool:
    try:
        wanted = _key(user_id, book_id)
    except (TypeError, ValueError):
        return False
    with _pending_lock:
        if wanted[1] == 0:
            return any(key[0] == wanted[0] for key in _pending_keys)
        return wanted in _pending_keys or (wanted[0], 0) in _pending_keys


def _queue(user_id, username, *, book_id=None, fmt=None, anchor_text=None,
           include_native_only=True, manual=False):
    try:
        value = int(user_id)
        book_value = int(book_id) if book_id is not None else None
    except (TypeError, ValueError):
        return {"success": False, "queued": False, "reason": "invalid_target"}
    settings = (ub.session.query(ub.MoonReaderWebdavSettings)
                .filter(ub.MoonReaderWebdavSettings.user_id == value).first())
    if settings is None or not settings.enabled or not settings.password_encrypted or not settings.cache_path:
        return {"success": True, "queued": False, "reason": "disabled"}
    pending_key = _key(value, book_value)
    with _pending_lock:
        if pending_key in _pending_keys or (book_value is not None and (value, 0) in _pending_keys):
            return {"success": True, "queued": False, "pending": True}
        _pending_keys.add(pending_key)
    try:
        if manual:
            settings.sync_status = "queued"
            settings.last_sync_error = None
            ub.session.commit()
        WorkerThread.add(
            username,
            TaskMoonReaderSync(
                value, book_id=book_value, fmt=fmt, anchor_text=anchor_text,
                include_native_only=include_native_only, manual=manual,
            ),
            hidden=True,
        )
    except Exception:
        ub.session.rollback()
        with _pending_lock:
            _pending_keys.discard(pending_key)
        raise
    return {"success": True, "queued": True}


def queue_moonreader_sync(user_id, username="System"):
    return _queue(user_id, username, include_native_only=True, manual=True)


def queue_moonreader_poll(user_id, username="System"):
    return _queue(user_id, username, include_native_only=True, manual=False)


def queue_moonreader_book_sync(user_id, book_id, fmt, *, anchor_text=None,
                               username="System"):
    anchor = " ".join(str(anchor_text or "").split())[:1000] or None
    return _queue(
        user_id, username, book_id=book_id, fmt=fmt, anchor_text=anchor,
        include_native_only=True, manual=False,
    )


class TaskMoonReaderSync(CalibreTask):
    def __init__(self, user_id: int, *, book_id=None, fmt=None, anchor_text=None,
                 include_native_only=True, manual=False):
        super().__init__(N_("Synchronizing Moon+ Reader positions"))
        self.user_id = int(user_id)
        self.book_id = int(book_id) if book_id is not None else None
        self.format = str(fmt or "").upper() or None
        self.anchor_text = anchor_text
        self.include_native_only = bool(include_native_only)
        self.manual = bool(manual)
        self.self_cleanup = True

    @property
    def name(self):
        return str(N_("Moon+ Reader position sync"))

    @property
    def is_cancellable(self):
        return True

    def _settings(self):
        return (ub.session.query(ub.MoonReaderWebdavSettings)
                .filter(ub.MoonReaderWebdavSettings.user_id == self.user_id).first())

    def run(self, worker_thread):
        from cps.services.moonreader_webdav import sync_positions

        pending_key = _key(self.user_id, self.book_id)
        try:
            settings = self._settings()
            if settings is None:
                return self._handleError("Moon+ Reader WebDAV settings no longer exist")
            if not settings.enabled:
                return self._handleError("Moon+ Reader sync is disabled")
            if self.manual:
                settings.sync_status = "running"
                settings.last_sync_error = None
                ub.session.commit()
            if self.stat in (STAT_CANCELLED, STAT_ENDED):
                if self.manual:
                    settings.sync_status = "idle"
                    ub.session.commit()
                return

            summary = sync_positions(
                self.user_id, book_id=self.book_id, fmt=self.format,
                anchor_text=self.anchor_text,
                include_native_only=self.include_native_only,
            )
            settings = self._settings()
            if settings is not None:
                settings.last_sync_at = datetime.now(timezone.utc)
                settings.last_sync_error = None
                settings.last_sync_summary = summary
                if self.manual:
                    settings.sync_status = "success"
                ub.session.commit()
            self.progress = 1
            self._handleSuccess()
        except Exception as exc:
            ub.session.rollback()
            log.exception("Moon+ Reader WebDAV sync failed for user %s book %s",
                          self.user_id, self.book_id)
            try:
                settings = self._settings()
                if settings is not None:
                    settings.last_sync_at = datetime.now(timezone.utc)
                    settings.last_sync_error = str(exc)[:2048]
                    if self.manual:
                        settings.sync_status = "error"
                    ub.session.commit()
            except Exception:
                ub.session.rollback()
            self._handleError(str(exc))
        finally:
            # WorkerThread is a real OS thread and does not get Flask request
            # teardown. Drop this thread/greenlet's app.db scoped Session after
            # each reconciliation so transaction state never survives a task.
            try:
                if ub.session is not None and hasattr(ub.session, "remove"):
                    ub.session.remove()
            except Exception:
                pass
            with _pending_lock:
                _pending_keys.discard(pending_key)


def start_moonreader_polling(app, *, seconds: int = 60):
    """Schedule portable WebDAV polling; writes stay serialized by WorkerThread."""
    global _poll_started
    with _pending_lock:
        if _poll_started:
            return
        _poll_started = True
    try:
        from cps.services.background_scheduler import BackgroundScheduler, IntervalTrigger
        scheduler = BackgroundScheduler()
        if not scheduler:
            return

        def poll():
            with app.app_context():
                try:
                    rows = (ub.session.query(ub.MoonReaderWebdavSettings)
                            .filter(ub.MoonReaderWebdavSettings.enabled.is_(True)).all())
                    for row in rows:
                        if row.password_encrypted and row.cache_path:
                            user = ub.session.get(ub.User, int(row.user_id))
                            queue_moonreader_poll(
                                int(row.user_id), getattr(user, "name", "System"))
                except Exception:
                    ub.session.rollback()
                    log.exception("Moon+ Reader polling tick failed")

        scheduler.scheduler.add_job(
            poll, trigger=IntervalTrigger(seconds=max(30, int(seconds))),
            id="moonreader-webdav-poll", name="Moon+ Reader WebDAV poll",
            replace_existing=True, coalesce=True, max_instances=1,
        )
    except Exception:
        with _pending_lock:
            _poll_started = False
        log.exception("Could not start Moon+ Reader WebDAV polling")
