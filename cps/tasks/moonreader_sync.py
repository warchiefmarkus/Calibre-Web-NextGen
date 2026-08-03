# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background Moon+ Reader WebDAV position import."""
from datetime import datetime, timezone
from threading import Lock

from flask_babel import lazy_gettext as N_

from cps import calibre_db, logger, ub
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED, WorkerThread

log = logger.create()
_pending_lock = Lock()
_pending_user_ids: set[int] = set()


def moonreader_sync_pending(user_id) -> bool:
    try:
        value = int(user_id)
    except (TypeError, ValueError):
        return False
    with _pending_lock:
        return value in _pending_user_ids


def queue_moonreader_sync(user_id, username="System"):
    try:
        value = int(user_id)
    except (TypeError, ValueError):
        return {"success": False, "queued": False, "reason": "invalid_user"}
    with _pending_lock:
        if value in _pending_user_ids:
            return {"success": True, "queued": False, "pending": True}
        _pending_user_ids.add(value)
    try:
        settings = (ub.session.query(ub.MoonReaderWebdavSettings)
                    .filter(ub.MoonReaderWebdavSettings.user_id == value).first())
        if settings is not None:
            settings.sync_status = "queued"
            settings.last_sync_error = None
            ub.session.commit()
        WorkerThread.add(username, TaskMoonReaderSync(value), hidden=True)
    except Exception:
        ub.session.rollback()
        with _pending_lock:
            _pending_user_ids.discard(value)
        raise
    return {"success": True, "queued": True}


class TaskMoonReaderSync(CalibreTask):
    def __init__(self, user_id: int):
        super().__init__(N_("Importing Moon+ Reader positions"))
        self.user_id = int(user_id)
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

        try:
            try:
                ub.init_db_thread()
            except Exception:
                pass
            calibre_db.ensure_session()
            settings = self._settings()
            if settings is None:
                return self._handleError("Moon+ Reader WebDAV settings no longer exist")
            if not settings.enabled:
                return self._handleError("Moon+ Reader sync is disabled")
            settings.sync_status = "running"
            settings.last_sync_error = None
            ub.session.commit()
            if self.stat in (STAT_CANCELLED, STAT_ENDED):
                settings.sync_status = "idle"
                ub.session.commit()
                return

            summary = sync_positions(self.user_id)
            settings = self._settings()
            if settings is not None:
                settings.sync_status = "success"
                settings.last_sync_at = datetime.now(timezone.utc)
                settings.last_sync_error = None
                settings.last_sync_summary = summary
                ub.session.commit()
            self.progress = 1
            self._handleSuccess()
        except Exception as exc:
            ub.session.rollback()
            log.exception("Moon+ Reader WebDAV sync failed for user %s", self.user_id)
            try:
                settings = self._settings()
                if settings is not None:
                    settings.sync_status = "error"
                    settings.last_sync_at = datetime.now(timezone.utc)
                    settings.last_sync_error = str(exc)[:2048]
                    ub.session.commit()
            except Exception:
                ub.session.rollback()
                log.exception("Could not persist Moon+ Reader sync failure state")
            self._handleError(str(exc))
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
                _pending_user_ids.discard(self.user_id)
