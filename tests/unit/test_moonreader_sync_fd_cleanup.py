# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_worker_task_does_not_open_calibre_session(monkeypatch):
    from cps import calibre_db
    from cps.tasks import moonreader_sync as mod
    from cps.services import moonreader_webdav

    class FakeUbSession:
        def commit(self):
            pass

        def rollback(self):
            pass

    settings = SimpleNamespace(
        enabled=True, last_sync_at=None, last_sync_error=None,
        last_sync_summary=None,
    )
    task = mod.TaskMoonReaderSync(1)
    monkeypatch.setattr(task, "_settings", lambda: settings)
    monkeypatch.setattr(mod.ub, "session", FakeUbSession())
    monkeypatch.setattr(
        calibre_db, "ensure_session",
        lambda *_a, **_kw: pytest.fail("worker opened Calibre DB session"),
    )
    monkeypatch.setattr(moonreader_webdav, "sync_positions", lambda *_a, **_kw: {})

    task.run(None)

    assert task.error is None
