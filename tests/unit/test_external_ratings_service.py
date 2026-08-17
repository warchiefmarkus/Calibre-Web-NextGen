# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import json
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
    with patch.object(service, "fetch_goodreads", side_effect=service.ProviderUnavailable("blocked")), \
         patch.object(service, "fetch_hardcover", side_effect=service.ProviderUnavailable("no token")), \
         patch.object(service, "fetch_google_books", return_value=ok), \
         patch.object(service, "fetch_open_library", side_effect=requests.Timeout()):
        outcomes = service.fetch_external_ratings(lookup())
    assert [item.source for item in outcomes] == list(service.SOURCE_ORDER)
    assert [item.status for item in outcomes] == ["unavailable", "unavailable", "ok", "error"]


def test_original_title_and_author_are_preferred_for_matching():
    from cps.services.external_ratings import candidate_confidence
    value = lookup(
        title="Властелины Doom",
        authors=("Дэвид Кушнер",),
        original_title="Masters of Doom",
        original_authors=("David Kushner",),
    )
    score, matched_by = candidate_confidence(
        value, "Masters of Doom", ["David Kushner"], [], 2003,
    )
    assert score >= 0.95
    assert matched_by == "original_title_author"


def test_google_queries_original_identity_before_localised_identity():
    from cps.services import external_ratings as service
    value = lookup(
        title="Зачем мы спим",
        authors=("Мэттью Уолкер",),
        original_title="Why We Sleep",
        original_authors=("Matthew Walker",),
    )
    responses = [
        Response({"items": []}),
        Response({"items": [{
            "id": "canonical-volume",
            "volumeInfo": {
                "title": "Why We Sleep",
                "authors": ["Matthew Walker"],
                "averageRating": 4.1,
                "ratingsCount": 500,
            },
        }]}),
    ]
    with patch.object(service.requests, "get", side_effect=responses) as request:
        result = service.fetch_google_books(value, "api-key")
    assert result.source_id == "canonical-volume"
    assert request.call_args_list[0].kwargs["params"]["q"].startswith("isbn:")
    original_query = request.call_args_list[1].kwargs["params"]["q"]
    assert "Why We Sleep" in original_query
    assert "Matthew Walker" in original_query
    assert "Зачем мы спим" not in original_query


def test_goodreads_apollo_state_returns_original_work_identity_and_stats():
    from cps.services import external_ratings as service
    state = {
        "ROOT_QUERY": {
            'getBookByLegacyId({"legacyId":"24273867"})': {"__ref": "Book:current"},
            'getSocialSignals({"bookId":"current"})': [
                {"name": "CURRENTLY_READING", "count": 1165},
                {"name": "TO_READ", "count": 20516},
            ],
        },
        "Book:current": {
            "__typename": "Book", "legacyId": 24273867,
            "title": "Властелины Doom",
            "webUrl": "https://www.goodreads.com/book/show/24273867-doom",
            "details": {"isbn13": "9785000573723"},
            "work": {"__ref": "Work:current"},
            "primaryContributorEdge": {"node": {"__ref": "Contributor:author"}},
        },
        "Work:current": {
            "__typename": "Work", "legacyId": 215133,
            "details": {
                "originalTitle": "Masters of Doom: How Two Guys Created an Empire",
                "webUrl": "https://www.goodreads.com/work/215133-masters-of-doom",
            },
            "stats": {
                "averageRating": 4.3, "ratingsCount": 20557,
                "textReviewsCount": 1591,
                "ratingsCountDist": [193, 379, 2366, 7835, 9784],
            },
        },
        "Contributor:author": {"__typename": "Contributor", "name": "David Kushner"},
    }
    payload = {"props": {"pageProps": {"apolloState": state}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
    value = lookup(
        title="Властелины Doom", authors=("Дэвид Кушнер",),
        isbn="9785000573723",
    )
    result = service._goodreads_parse(
        html, "https://www.goodreads.com/book/show/24273867-doom", value, "isbn",
    )
    assert result.matched_title.startswith("Masters of Doom")
    assert result.matched_authors == ["David Kushner"]
    assert result.rating == 4.3
    assert result.ratings_count == 20557
    assert result.reviews_count == 1591
    assert result.popularity_count == 21681
    assert result.ratings_distribution["5"] == 9784


def test_goodreads_canonical_identity_enriches_other_provider_queries():
    from cps.services import external_ratings as service
    goodreads = service.ExternalRatingResult(
        source="goodreads", source_id="215133",
        source_url="https://www.goodreads.com/work/215133",
        matched_title="Masters of Doom",
        matched_authors=["David Kushner"],
        rating=4.3,
    )
    provider_result = service.ExternalRatingResult(
        source="provider", source_id="1", source_url="https://example.test/book",
        matched_title="Masters of Doom", rating=4.0,
    )
    with patch.object(service, "fetch_goodreads", return_value=goodreads), \
         patch.object(service, "fetch_hardcover", return_value=provider_result) as hardcover, \
         patch.object(service, "fetch_google_books", return_value=provider_result) as google, \
         patch.object(service, "fetch_open_library", return_value=provider_result) as open_library:
        outcomes = service.fetch_external_ratings(
            lookup(title="Властелины Doom", authors=("Дэвид Кушнер",)),
            ["hardcover-token"], "google-key",
        )
    assert all(outcome.status == "ok" for outcome in outcomes)
    for called_lookup in (
        hardcover.call_args.args[0], google.call_args.args[0], open_library.call_args.args[0],
    ):
        assert called_lookup.original_title == "Masters of Doom"
        assert called_lookup.original_authors == ("David Kushner",)
    assert google.call_args.args[1] == "google-key"


def test_goodreads_legacy_id_uses_isbn_redirect_without_loading_challenge_page():
    from types import SimpleNamespace
    from cps.services import external_ratings as service

    response = SimpleNamespace(
        status_code=301,
        headers={"Location": "https://www.goodreads.com/book/show/24273867-doom"},
        url="https://www.goodreads.com/book/isbn/9785000573723",
        raise_for_status=lambda: None,
    )
    with patch.object(service.requests, "get", return_value=response) as request:
        legacy_id, matched_by = service._goodreads_legacy_id(
            lookup(isbn="9785000573723"),
        )
    assert legacy_id == 24273867
    assert matched_by == "isbn"
    assert request.call_args.kwargs["allow_redirects"] is False


def test_goodreads_graphql_returns_canonical_work_from_localised_edition():
    from cps.services import external_ratings as service

    data = {
        "getBookByLegacyId": {
            "legacyId": 24273867,
            "title": "Властелины Doom",
            "details": {"isbn13": "9785000573723"},
            "primaryContributorEdge": {"node": {"id": "author-1", "name": "David Kushner"}},
            "work": {
                "legacyId": 215133,
                "details": {
                    "originalTitle": "Masters of Doom",
                    "webUrl": "https://www.goodreads.com/work/215133-masters-of-doom",
                },
                "stats": {
                    "averageRating": 4.3,
                    "ratingsCount": 20557,
                    "textReviewsCount": 1591,
                    "ratingsCountDist": [193, 379, 2366, 7835, 9784],
                },
                "bestBook": {
                    "legacyId": 222146,
                    "title": "Masters of Doom",
                    "primaryContributorEdge": {"node": {"id": "author-1", "name": "David Kushner"}},
                },
            },
        },
    }
    value = lookup(
        title="Властелины Doom",
        authors=("Дэвид Кушнер",),
        isbn="9785000573723",
    )
    with patch.object(service, "_goodreads_graphql_data", return_value=data):
        result = service._goodreads_graphql_result(value, 24273867, "isbn")
    assert result.matched_title == "Masters of Doom"
    assert result.matched_authors == ["David Kushner"]
    assert result.rating == 4.3
    assert result.ratings_count == 20557
    assert result.matched_by == "isbn"


def test_goodreads_author_work_fallback_expands_distinctive_acronym():
    from cps.services import external_ratings as service

    current_book = {
        "legacyId": 106805869,
        "title": "В угоне. Подлинная история GTA",
        "details": {"isbn13": "9785367043785"},
        "primaryContributorEdge": {"node": {"id": "author-david", "name": "David Kushner"}},
        "work": {
            "legacyId": 129900997,
            "details": {"originalTitle": ""},
            "stats": {"averageRating": 0.0, "ratingsCount": 0, "textReviewsCount": 0},
            "bestBook": {"title": "В угоне. Подлинная история GTA"},
        },
    }
    author_works = {
        "getWorksByContributor": {
            "edges": [
                {"node": {
                    "id": "work-doom",
                    "stats": {"averageRating": 4.3, "ratingsCount": 20557, "textReviewsCount": 1591},
                    "bestBook": {
                        "legacyId": 222146,
                        "title": "Masters of Doom",
                        "webUrl": "https://www.goodreads.com/book/show/222146.Masters_of_Doom",
                        "primaryContributorEdge": {"node": {"name": "David Kushner"}},
                    },
                }},
                {"node": {
                    "id": "work-jacked",
                    "stats": {
                        "averageRating": 3.62,
                        "ratingsCount": 1955,
                        "textReviewsCount": 188,
                        "ratingsCountDist": [26, 175, 678, 710, 366],
                    },
                    "bestBook": {
                        "legacyId": 13074587,
                        "title": "Jacked: The Outlaw Story of Grand Theft Auto",
                        "webUrl": "https://www.goodreads.com/book/show/13074587-jacked",
                        "primaryContributorEdge": {"node": {"name": "David Kushner"}},
                    },
                }},
            ],
        },
    }
    value = lookup(
        title="В угоне. Подлинная история GTA",
        authors=("Дэвид Кушнер",),
        isbn="9785367043785",
    )
    with patch.object(
        service, "_goodreads_graphql_data",
        side_effect=[{"getBookByLegacyId": current_book}, author_works],
    ):
        result = service._goodreads_graphql_result(value, 106805869, "isbn")
    assert result.matched_title == "Jacked: The Outlaw Story of Grand Theft Auto"
    assert result.matched_authors == ["David Kushner"]
    assert result.rating == 3.62
    assert result.ratings_count == 1955
    assert result.matched_by == "author_work_marker"
    assert result.match_confidence >= 0.9


def test_goodreads_search_candidate_validates_localised_title_before_canonical_lookup():
    from cps.services import external_ratings as service

    html = '''
    <table><tr itemtype="http://schema.org/Book">
      <td><a class="bookTitle" href="/book/show/40736526?rank=1">
        Кровь, пот и пиксели. Обратная сторона индустрии видеоигр
      </a></td>
      <td><a class="authorName"><span itemprop="name">Jason Schreier</span></a>
      published 2017
      </td>
    </tr></table>
    '''
    value = lookup(
        title="Кровь, пот и пиксели. Обратная сторона индустрии видеоигр",
        authors=("Джейсон Шрейер",),
        publication_year=2017,
    )
    candidates = service._goodreads_search_candidates(html, value)
    assert candidates[0][0] == 40736526
    assert candidates[0][2] >= 0.7
    assert candidates[0][3] == "title_author"


def test_goodreads_graphql_search_validates_localised_result_without_html():
    from cps.services import external_ratings as service

    data = {
        "getSearchSuggestions": {
            "edges": [{
                "node": {
                    "id": "book-kca",
                    "title": "Кровь, пот и пиксели. Обратная сторона индустрии видеоигр",
                    "primaryContributorEdge": {"node": {"name": "Jason Schreier"}},
                    "webUrl": "https://www.goodreads.com/book/show/40736526",
                },
            }],
        },
    }
    value = lookup(
        title="Кровь, пот и пиксели. Обратная сторона индустрии видеоигр",
        authors=("Джейсон Шрейер",),
    )
    with patch.object(service, "_goodreads_graphql_data", return_value=data) as request:
        candidates = service._goodreads_graphql_search_candidates(value)
    assert candidates[0][0] == 40736526
    assert candidates[0][1] >= 0.7
    assert candidates[0][2] == "title_author"
    assert request.call_args.args[0] == service.GOODREADS_SEARCH_QUERY


def test_goodreads_search_prefers_established_work_among_close_matches():
    from cps.services import external_ratings as service

    reprint = service.ExternalRatingResult(
        source="goodreads",
        source_id="reprint",
        source_url="https://www.goodreads.com/work/reprint",
        matched_title="Localized second edition",
        matched_authors=["Jason Schreier"],
        matched_by="title_author",
        match_confidence=0.771,
        rating=4.0,
        ratings_count=1,
        reviews_count=0,
    )
    canonical = service.ExternalRatingResult(
        source="goodreads",
        source_id="canonical",
        source_url="https://www.goodreads.com/work/canonical",
        matched_title="Blood, Sweat, and Pixels",
        matched_authors=["Jason Schreier"],
        matched_by="title_author",
        match_confidence=0.738,
        rating=4.2,
        ratings_count=25402,
        reviews_count=2370,
    )
    with patch.object(service, "_goodreads_legacy_id", return_value=(None, None)), \
         patch.object(service, "_goodreads_graphql_search_candidates", return_value=[
             (250540812, 0.771, "title_author"),
             (40736526, 0.738, "title_author"),
         ]), \
         patch.object(service, "_goodreads_graphql_result", side_effect=[reprint, canonical]):
        result = service.fetch_goodreads(lookup())
    assert result.source_id == "canonical"
    assert result.ratings_count == 25402
