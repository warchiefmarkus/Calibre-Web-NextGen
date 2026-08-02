# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import patch

import pytest
import requests

pytestmark = pytest.mark.unit


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def lookup(**overrides):
    from cps.services.external_ratings import BookLookup
    values = dict(
        book_id=7,
        title="The Example Book",
        authors=("Alex Writer",),
        isbn="9781234567897",
        publication_year=2024,
    )
    values.update(overrides)
    return BookLookup(**values)


def test_exact_isbn_wins_even_when_localised_title_differs():
    from cps.services.external_ratings import candidate_confidence
    score, matched_by = candidate_confidence(
        lookup(title="Локалізована назва"),
        "Completely Different Original Title",
        ["Another Name"],
        ["978-1-234-56789-7"],
    )
    assert score == 1.0
    assert matched_by == "isbn"


def test_lookup_hash_changes_with_provider_identity():
    base = lookup().identity_hash
    assert lookup(google_id="volume-1").identity_hash != base
    assert lookup(open_library_id="OL1W").identity_hash != base
    assert lookup(hardcover_id=42).identity_hash != base


def test_google_direct_identifier_parses_rating_and_uses_optional_key(monkeypatch):
    from cps.services import external_ratings as service
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "configured-key")
    get = Response({
        "id": "volume-1",
        "volumeInfo": {
            "title": "The Example Book",
            "authors": ["Alex Writer"],
            "averageRating": 4.25,
            "ratingsCount": 321,
            "infoLink": "https://books.google.test/volume-1",
        },
    })
    with patch.object(service.requests, "get", return_value=get) as request:
        result = service.fetch_google_books(lookup(google_id="volume-1"))
    assert result.rating == 4.25
    assert result.ratings_count == 321
    assert result.matched_by == "google_id"
    assert request.call_args.kwargs["params"]["key"] == "configured-key"


def test_google_falls_back_from_unmatched_isbn_to_title_author():
    from cps.services import external_ratings as service
    responses = [
        Response({"items": []}),
        Response({"items": [{
            "id": "volume-2",
            "volumeInfo": {
                "title": "The Example Book",
                "authors": ["Alex Writer"],
                "averageRating": 4.0,
                "ratingsCount": 12,
            },
        }]}),
    ]
    with patch.object(service.requests, "get", side_effect=responses) as request:
        result = service.fetch_google_books(lookup())
    assert result.source_id == "volume-2"
    assert request.call_count == 2
    assert request.call_args_list[0].kwargs["params"]["q"].startswith("isbn:")
    assert "intitle:" in request.call_args_list[1].kwargs["params"]["q"]


def test_open_library_parses_distribution_and_reading_log_popularity():
    from cps.services import external_ratings as service
    response = Response({"docs": [{
        "key": "/works/OL1W",
        "title": "The Example Book",
        "author_name": ["Alex Writer"],
        "isbn": ["9781234567897"],
        "ratings_average": 4.1,
        "ratings_count": 50,
        "ratings_count_1": 1,
        "ratings_count_2": 2,
        "ratings_count_3": 5,
        "ratings_count_4": 17,
        "ratings_count_5": 25,
        "readinglog_count": 900,
    }]})
    with patch.object(service.requests, "get", return_value=response):
        result = service.fetch_open_library(lookup())
    assert result.rating == 4.1
    assert result.ratings_count == 50
    assert result.popularity_count == 900
    assert result.ratings_distribution == {"1": 1, "2": 2, "3": 5, "4": 17, "5": 25}
    assert result.matched_by == "isbn"


def test_open_library_uses_work_level_rating_and_bookshelf_fallback():
    from cps.services import external_ratings as service
    response = Response({"docs": [{
        "key": "/works/OL1W",
        "title": "The Example Book",
        "author_name": ["Alex Writer"],
        "isbn": ["9781234567897"],
    }]})
    with patch.object(service.requests, "get", return_value=response), \
         patch.object(service, "_open_library_work_stats", return_value=(4.2, 25, 80, {"5": 12})):
        result = service.fetch_open_library(lookup())
    assert result.rating == 4.2
    assert result.ratings_count == 25
    assert result.popularity_count == 80
    assert result.ratings_distribution == {"5": 12}


def test_hardcover_search_falls_back_from_isbn_to_title_author():
    from cps.services import external_ratings as service
    empty = {"search": {"results": {"hits": []}}}
    matched = {"search": {"results": {"hits": [{"document": {
        "id": 88,
        "title": "The Example Book",
        "author_names": ["Alex Writer"],
        "isbns": [],
        "release_year": 2024,
    }}]}}}
    with patch.object(service, "_hardcover_request", side_effect=[empty, matched]) as request:
        book_id, confidence, matched_by = service._hardcover_search_id(lookup(), ["token"])
    assert book_id == 88
    assert confidence >= 0.9
    assert matched_by == "title_author"
    assert request.call_count == 2
    assert request.call_args_list[0].args[1]["query"] == "9781234567897"
    assert "The Example Book" in request.call_args_list[1].args[1]["query"]


def test_hardcover_without_token_is_reported_as_unavailable():
    from cps.services import external_ratings as service
    outcome = service._run_provider("hardcover", lambda: service.fetch_hardcover(lookup(), []))
    assert outcome.status == "unavailable"
    assert "token" in outcome.error.lower()


def test_provider_failure_does_not_cancel_other_sources():
    from cps.services import external_ratings as service
    ok = service.ExternalRatingResult(
        source="google_books", source_id="g", source_url="https://example.test/g",
        matched_title="The Example Book", rating=4.0,
    )
    with patch.object(service, "fetch_hardcover", side_effect=service.ProviderUnavailable("no token")), \
         patch.object(service, "fetch_google_books", return_value=ok), \
         patch.object(service, "fetch_open_library", side_effect=requests.Timeout()):
        outcomes = service.fetch_external_ratings(lookup())
    assert [item.source for item in outcomes] == list(service.SOURCE_ORDER)
    assert [item.status for item in outcomes] == ["unavailable", "ok", "error"]
