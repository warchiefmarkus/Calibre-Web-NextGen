# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import MagicMock, patch
import pytest

pytestmark = pytest.mark.unit


def test_queue_moonreader_sync_deduplicates_user_and_hides_task():
    from cps.tasks import moonreader_sync as mod
    mod._pending_user_ids.clear()
    settings = MagicMock(sync_status="idle")
    query = MagicMock()
    query.filter.return_value.first.return_value = settings
    session = MagicMock()
    session.query.return_value = query
    with patch.object(mod.ub, "session", session), \
         patch.object(mod.WorkerThread, "add") as add:
        first = mod.queue_moonreader_sync(4, "alice")
        second = mod.queue_moonreader_sync(4, "alice")
    try:
        assert first["queued"] is True
        assert second["pending"] is True
        assert add.call_count == 1
        assert add.call_args.kwargs["hidden"] is True
        assert add.call_args.args[1].user_id == 4
        assert settings.sync_status == "queued"
    finally:
        mod._pending_user_ids.clear()
