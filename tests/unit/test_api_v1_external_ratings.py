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
            SimpleNamespace(type="hardcover-id", val="42"),
        ],
        pubdate=datetime(2024, 1, 1),
    )


def test_book_lookup_extracts_provider_identifiers_and_normalizes_author():
    from cps.api import external_ratings as api
    value = api._book_lookup(_book())
    assert value.isbn == "9781234567897"
    assert value.authors == ("Author,Name",)
    assert value.google_id == "google-id"
    assert value.hardcover_id == 42
    assert value.publication_year == 2024


def test_get_route_returns_cached_or_refreshed_payload():
    from cps.api import external_ratings as api
    expected = {"items": [], "errors": [], "warnings": [], "cached": True, "fetched_at": None}
    with _ctx("/api/v1/books/5/external-ratings"), \
         patch.object(api, "_load_or_refresh", return_value=expected) as loader:
        response = inspect.unwrap(api.external_book_ratings)(5)
    assert json.loads(response.get_data()) == expected
    loader.assert_called_once_with(5)


def test_refresh_route_forces_provider_refresh():
    from cps.api import external_ratings as api
    expected = {"items": [], "errors": [], "warnings": [], "cached": False, "fetched_at": None}
    with _ctx("/api/v1/books/5/external-ratings/refresh", method="POST"), \
         patch.object(api, "_load_or_refresh", return_value=expected) as loader:
        response = inspect.unwrap(api.refresh_external_book_ratings)(5)
    assert json.loads(response.get_data()) == expected
    loader.assert_called_once_with(5, force=True)


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
        for source in ("hardcover", "google_books", "open_library")
    ]
    assert api._cache_is_fresh(rows, "hash") is True
    assert api._cache_is_fresh(rows[:-1], "hash") is False
    assert api._cache_is_fresh(rows, "changed") is False
