# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import inspect
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

pytestmark = pytest.mark.unit


def _ctx(path, method="GET"):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context(path, method=method)


def _book():
    return SimpleNamespace(
        id=5,
        title="Book",
        authors=[SimpleNamespace(name="Author|Name")],
        identifiers=[
            SimpleNamespace(type="isbn", val="978-1-234-56789-7"),
            SimpleNamespace(type="google", val="google-id"),
            SimpleNamespace(type="goodreads", val="123"),
            SimpleNamespace(type="original-title", val="Original Book"),
            SimpleNamespace(type="original-author", val="Original Author & Second Author"),
            SimpleNamespace(type="hardcover-id", val="42"),
        ],
        pubdate=datetime(2024, 1, 1),
    )


def test_book_lookup_extracts_provider_identifiers_and_normalizes_author():
    from cps.api import external_ratings as api
    value = api._book_lookup(_book())
    assert value.isbn == "9781234567897"
    assert value.authors == ("Author,Name",)
    assert value.original_title == "Original Book"
    assert value.original_authors == ("Original Author", "Second Author")
    assert value.goodreads_id == "123"
    assert value.google_id == "google-id"
    assert value.hardcover_id == 42
    assert value.publication_year == 2024


def test_get_route_returns_cached_payload_without_sync_provider_fetch():
    from cps.api import external_ratings as api
    expected = {
        "items": [], "errors": [], "warnings": [], "cached": True,
        "fetched_at": None, "refreshing": False,
    }
    with _ctx("/api/v1/books/5/external-ratings"), \
         patch.object(api, "load_cached_external_ratings", return_value=expected) as loader:
        response = inspect.unwrap(api.external_book_ratings)(5)
    assert json.loads(response.get_data()) == expected
    loader.assert_called_once_with(5)



def test_async_refresh_route_does_not_queue_when_rating_exists():
    from cps.api import external_ratings as api
    from cps.tasks import external_ratings as tasks

    cached = {
        "items": [{"source": "goodreads", "rating": 4.2}],
        "errors": [], "warnings": [], "cached": True,
        "fetched_at": "2026-08-03T08:00:00+00:00", "refreshing": False,
    }
    with _ctx("/api/v1/books/5/external-ratings/refresh-async", method="POST"), \
         patch.object(api, "load_cached_external_ratings", return_value=cached.copy()), \
         patch.object(tasks, "queue_external_rating_refresh") as queue, \
         patch.object(tasks, "external_rating_refresh_pending", return_value=False):
        response, status = inspect.unwrap(api.refresh_external_book_ratings_async)(5)

    assert status == 200
    assert json.loads(response.get_data())["refreshing"] is False
    queue.assert_not_called()


def test_async_refresh_route_queues_missing_rating_after_one_day():
    from cps.api import external_ratings as api
    from cps.tasks import external_ratings as tasks

    cached = {
        "items": [], "errors": [], "warnings": [], "cached": True,
        "fetched_at": "2026-08-01T08:00:00+00:00", "refreshing": False,
    }
    user = SimpleNamespace(name="alice")
    queued = {"success": True, "queued": True, "book_ids": [5]}
    with _ctx("/api/v1/books/5/external-ratings/refresh-async", method="POST"), \
         patch.object(api, "load_cached_external_ratings", return_value=cached.copy()), \
         patch.object(api, "_missing_rating_retry_due", return_value=True) as retry_due, \
         patch.object(api, "current_user", user), \
         patch.object(api, "_hardcover_tokens", return_value=[]), \
         patch.object(api, "_google_books_api_key", return_value=None), \
         patch.object(tasks, "queue_external_rating_refresh", return_value=queued) as queue, \
         patch.object(tasks, "external_rating_refresh_pending", return_value=False):
        response, status = inspect.unwrap(api.refresh_external_book_ratings_async)(5)

    assert status == 202
    assert json.loads(response.get_data())["refreshing"] is True
    retry_due.assert_called_once()
    queue.assert_called_once_with(
        [5], username="alice", hardcover_tokens=[], google_books_api_key=None,
        force=True,
    )


def test_async_refresh_route_skips_recent_missing_rating_attempt():
    from cps.api import external_ratings as api
    from cps.tasks import external_ratings as tasks

    cached = {
        "items": [], "errors": [], "warnings": [], "cached": True,
        "fetched_at": "2026-08-03T07:30:00+00:00", "refreshing": False,
    }
    with _ctx("/api/v1/books/5/external-ratings/refresh-async", method="POST"), \
         patch.object(api, "load_cached_external_ratings", return_value=cached.copy()), \
         patch.object(api, "_missing_rating_retry_due", return_value=False), \
         patch.object(tasks, "queue_external_rating_refresh") as queue, \
         patch.object(tasks, "external_rating_refresh_pending", return_value=False):
        response, status = inspect.unwrap(api.refresh_external_book_ratings_async)(5)

    assert status == 200
    assert json.loads(response.get_data())["refreshing"] is False
    queue.assert_not_called()


def test_missing_rating_retry_due_uses_persisted_daily_interval():
    from cps.api import external_ratings as api

    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    missing = {"items": [], "fetched_at": None}
    recent = {"items": [], "fetched_at": "2026-08-02T12:00:00+00:00"}
    old = {"items": [], "fetched_at": "2026-08-02T07:59:59+00:00"}
    rated = {"items": [{"rating": 4.2}], "fetched_at": None}

    assert api._missing_rating_retry_due(missing, now=now) is True
    assert api._missing_rating_retry_due(recent, now=now) is False
    assert api._missing_rating_retry_due(old, now=now) is True
    assert api._missing_rating_retry_due(rated, now=now) is False


def test_refresh_route_forces_provider_refresh():
    from cps.api import external_ratings as api
    expected = {"items": [], "errors": [], "warnings": [], "cached": False, "fetched_at": None}
    with _ctx("/api/v1/books/5/external-ratings/refresh", method="POST"), \
         patch.object(api, "_load_or_refresh", return_value=expected) as loader:
        response = inspect.unwrap(api.refresh_external_book_ratings)(5)
    assert json.loads(response.get_data()) == {**expected, "refreshing": False}
    loader.assert_called_once_with(5, force=True)



def test_cached_get_never_queues_provider_refresh():
    from cps.api import external_ratings as api
    from cps.tasks import external_ratings as tasks

    cached = {
        "items": [{"source": "goodreads", "rating": 4.2}],
        "errors": [], "warnings": [], "cached": True, "fetched_at": "old",
    }
    with patch.object(api.calibre_db, "get_filtered_book", return_value=_book()), \
         patch.object(api, "_cache_rows", return_value=[SimpleNamespace()]), \
         patch.object(api, "_serialize", return_value=cached.copy()), \
         patch.object(tasks, "external_rating_refresh_pending", return_value=False), \
         patch.object(tasks, "queue_external_rating_refresh") as queue:
        payload = api.load_cached_external_ratings(5)

    assert payload["items"][0]["rating"] == 4.2
    assert payload["refreshing"] is False
    queue.assert_not_called()


def test_transient_provider_error_preserves_last_successful_aggregate():
    from cps.api import external_ratings as api
    from cps.services.external_ratings import ProviderOutcome
    row = SimpleNamespace(
        source="google_books", status="ok", lookup_hash="same", error=None,
        fetched_at=datetime(2024, 1, 1, tzinfo=timezone.utc), rating=4.5,
    )
    mock_session = MagicMock()
    with patch.object(api, "_cache_rows", return_value=[row]), \
         patch.object(api.ub, "session", mock_session):
        rows = api._persist_outcomes(5, "same", [
            ProviderOutcome(source="google_books", status="error", error="timeout"),
        ])
    assert rows == [row]
    assert row.status == "ok"
    assert row.rating == 4.5
    assert row.error == "timeout"
    mock_session.commit.assert_called_once()


def test_cache_freshness_requires_all_sources_and_same_identity():
    from cps.api import external_ratings as api
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(source=source, status="ok", lookup_hash="hash", fetched_at=now)
        for source in ("goodreads", "hardcover", "google_books", "open_library")
    ]
    assert api._cache_is_fresh(rows, "hash") is True
    assert api._cache_is_fresh(rows[:-1], "hash") is False
    assert api._cache_is_fresh(rows, "changed") is False


def test_google_books_api_key_uses_admin_configuration():
    from cps.api import external_ratings as api
    with patch.object(api.config, "config_google_books_api_key", "ui-key", create=True):
        assert api._google_books_api_key() == "ui-key"


def test_summary_prefers_established_source_over_one_vote_edition():
    from cps.api import external_ratings as api
    rows = [
        SimpleNamespace(
            book_id=5, source="goodreads", rating=4.2, ratings_count=25402,
            reviews_count=2370,
        ),
        SimpleNamespace(
            book_id=5, source="hardcover", rating=4.8, ratings_count=1,
            reviews_count=0,
        ),
    ]
    query = MagicMock()
    query.filter.return_value = query
    query.all.return_value = rows
    session = MagicMock()
    session.query.return_value = query
    with patch.object(api.ub, "session", session):
        result = api.external_rating_summary_map([5, 5, "bad"])
    assert result == {
        5: {
            "source": "goodreads",
            "rating": 4.2,
            "ratings_count": 25402,
            "source_count": 2,
        }
    }
