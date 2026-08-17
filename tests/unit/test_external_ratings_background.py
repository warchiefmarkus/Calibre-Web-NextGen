# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_queue_external_ratings_deduplicates_ids_and_hides_task():
    from cps.tasks import external_ratings as tasks
    tasks._pending_book_ids.clear()
    try:
        with patch.object(tasks.WorkerThread, "add") as add:
            result = tasks.queue_external_rating_refresh([7, "7", None, 8, -1])
            duplicate = tasks.queue_external_rating_refresh([7, 8])
        assert result == {"success": True, "queued": True, "book_ids": [7, 8]}
        assert duplicate == {
            "success": True, "queued": False, "pending": True, "book_ids": [7, 8],
        }
        queued_task = add.call_args.args[1]
        assert queued_task.book_ids == [7, 8]
        assert add.call_args.kwargs["hidden"] is True
        assert add.call_count == 1
    finally:
        tasks._pending_book_ids.clear()


def test_background_task_populates_each_book_without_forcing_refresh():
    from cps.tasks import external_ratings as tasks
    loader = MagicMock(return_value={"cached": False})
    task = tasks.TaskExternalRatings(
        [7, 8],
        hardcover_tokens=["hc"],
        google_books_api_key="gb",
    )
    with patch("cps.api.external_ratings.load_or_refresh_external_ratings", loader), \
         patch.object(tasks.ub, "init_db_thread"), \
         patch.object(tasks.calibre_db, "ensure_session"), \
         patch.object(tasks.calibre_db, "session", MagicMock()):
        task.run(None)
    assert [call.args[0] for call in loader.call_args_list] == [7, 8]
    assert all(call.kwargs["hardcover_tokens"] == ["hc"] for call in loader.call_args_list)
    assert all(call.kwargs["google_books_api_key"] == "gb" for call in loader.call_args_list)
    assert all(call.kwargs["unfiltered"] is True for call in loader.call_args_list)
    assert all(call.kwargs["force"] is False for call in loader.call_args_list)


def test_both_import_paths_queue_external_ratings_after_book_ids_exist():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    upload = (root / "cps/api/upload.py").read_text(encoding="utf-8")
    ingest = (root / "scripts/ingest_processor.py").read_text(encoding="utf-8")

    assert "queue_external_rating_refresh(" in upload
    assert '[item["book_id"] for item in imported]' in upload
    assert "self.fetch_metadata_if_enabled" in ingest
    assert "queue_external_ratings_for_books(" in ingest
    metadata_hook = ingest.index("self.fetch_metadata_if_enabled")
    queued_hook = ingest.index("queue_external_ratings_for_books(", metadata_hook)
    assert metadata_hook < queued_hook


def test_forced_missing_rating_refresh_passes_force_to_task():
    from cps.tasks import external_ratings as tasks
    tasks._pending_book_ids.clear()
    try:
        with patch.object(tasks.WorkerThread, "add") as add:
            result = tasks.queue_external_rating_refresh([7], force=True)
        queued_task = add.call_args.args[1]

        assert result == {"success": True, "queued": True, "book_ids": [7]}
        assert queued_task.force is True
    finally:
        tasks._pending_book_ids.clear()
