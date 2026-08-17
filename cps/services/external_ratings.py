# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""External book rating and popularity providers.

The service is deliberately independent from Flask and SQLAlchemy. API routes
pass it a plain lookup object and persist the returned provider outcomes in
app.db. Providers return aggregate data only; user review text is not copied.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Callable, Iterable
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from .. import constants, logger

log = logger.create()

REQUEST_TIMEOUT = 8
SOURCE_ORDER = ("goodreads", "hardcover", "google_books", "open_library")


@dataclass(frozen=True)
class BookLookup:
    book_id: int
    title: str
    authors: tuple[str, ...] = ()
    original_title: str | None = None
    original_authors: tuple[str, ...] = ()
    isbn: str | None = None
    publication_year: int | None = None
    goodreads_id: str | None = None
    hardcover_id: int | None = None
    hardcover_slug: str | None = None
    google_id: str | None = None
    open_library_id: str | None = None

    @property
    def identity_hash(self) -> str:
        body = json.dumps({
            "title": normalize_text(self.title),
            "authors": [normalize_text(author) for author in self.authors],
            "original_title": normalize_text(self.original_title),
            "original_authors": [normalize_text(author) for author in self.original_authors],
            "isbn": normalize_isbn(self.isbn),
            "publication_year": self.publication_year,
            "goodreads_id": self.goodreads_id or "",
            "hardcover_id": self.hardcover_id,
            "hardcover_slug": self.hardcover_slug or "",
            "google_id": self.google_id or "",
            "open_library_id": self.open_library_id or "",
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @property
    def identity_variants(self) -> tuple[tuple[str, tuple[str, ...], str], ...]:
        variants: list[tuple[str, tuple[str, ...], str]] = []
        if self.original_title:
            variants.append((
                self.original_title,
                self.original_authors or self.authors,
                "original_title_author",
            ))
        variants.append((self.title, self.authors, "title_author"))

        unique: list[tuple[str, tuple[str, ...], str]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for title, authors, matched_by in variants:
            key = (normalize_text(title), tuple(normalize_text(author) for author in authors))
            if key[0] and key not in seen:
                seen.add(key)
                unique.append((title, authors, matched_by))
        return tuple(unique)

    def with_canonical(self, title: str | None, authors: Iterable[str] = ()) -> "BookLookup":
        clean_title = str(title or "").strip()
        clean_authors = tuple(str(author).strip() for author in authors if str(author).strip())
        if not clean_title:
            return self
        return replace(
            self,
            original_title=self.original_title or clean_title,
            original_authors=self.original_authors or clean_authors,
        )


@dataclass
class ExternalRatingResult:
    source: str
    source_id: str
    source_url: str
    matched_title: str
    matched_authors: list[str] = field(default_factory=list)
    matched_by: str = "title_author"
    match_confidence: float = 0.0
    rating: float | None = None
    ratings_count: int | None = None
    reviews_count: int | None = None
    popularity_count: int | None = None
    ratings_distribution: dict[str, int] | None = None


@dataclass
class ProviderOutcome:
    source: str
    status: str
    result: ExternalRatingResult | None = None
    error: str | None = None


class ProviderUnavailable(RuntimeError):
    pass


class ProviderNotFound(RuntimeError):
    pass


def normalize_isbn(value: Any) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^0-9Xx]", "", str(value)).upper()
    return normalized if len(normalized) in (10, 13) else None


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE))


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _safe_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _author_similarity(expected: Iterable[str], candidate: Iterable[str]) -> float:
    expected_norm = [normalize_text(value) for value in expected if normalize_text(value)]
    candidate_norm = [normalize_text(value) for value in candidate if normalize_text(value)]
    if not expected_norm or not candidate_norm:
        return 0.0
    scores = [
        max(SequenceMatcher(None, author, other).ratio() for other in candidate_norm)
        for author in expected_norm
    ]
    return sum(scores) / len(scores)


def candidate_confidence(
    lookup: BookLookup,
    title: str,
    authors: Iterable[str],
    isbns: Iterable[Any] = (),
    publication_year: Any = None,
) -> tuple[float, str]:
    expected_isbn = normalize_isbn(lookup.isbn)
    candidate_isbns = {value for value in (normalize_isbn(item) for item in isbns) if value}
    if expected_isbn and expected_isbn in candidate_isbns:
        return 1.0, "isbn"

    best_score = 0.0
    matched_by = "title_author"
    normalized_candidate_title = normalize_text(title)
    for expected_title, expected_authors, variant_match in lookup.identity_variants:
        title_score = SequenceMatcher(
            None, normalize_text(expected_title), normalized_candidate_title,
        ).ratio()
        author_score = _author_similarity(expected_authors, authors)
        score = title_score * 0.72 + author_score * 0.25
        if score > best_score:
            best_score = score
            matched_by = variant_match

    year = _safe_int(publication_year)
    if lookup.publication_year and year:
        delta = abs(lookup.publication_year - year)
        if delta == 0:
            best_score += 0.03
        elif delta == 1:
            best_score += 0.015

    return min(best_score, 1.0), matched_by


def _best_candidate(
    lookup: BookLookup,
    candidates: Iterable[dict[str, Any]],
    unpack: Callable[[dict[str, Any]], tuple[str, list[str], list[Any], Any]],
) -> tuple[dict[str, Any], float, str]:
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for candidate in candidates:
        title, authors, isbns, year = unpack(candidate)
        confidence, matched_by = candidate_confidence(
            lookup, title, authors, isbns, year,
        )
        ranked.append((confidence, matched_by, candidate))
    if not ranked:
        raise ProviderNotFound("No matching book was returned")
    confidence, matched_by, candidate = max(ranked, key=lambda item: item[0])
    if confidence < 0.62:
        raise ProviderNotFound("No sufficiently confident match was found")
    return candidate, confidence, matched_by


def _distribution(value: Any) -> dict[str, int] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for key, count in value.items():
        normalized_key = str(key).replace("stars", "").replace("star", "").strip()
        if normalized_key in {"1", "2", "3", "4", "5"}:
            parsed = _safe_int(count)
            if parsed is not None:
                result[normalized_key] = parsed
    return result or None


GOODREADS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.8",
}
GOODREADS_GRAPHQL_ENDPOINT = os.environ.get(
    "GOODREADS_GRAPHQL_ENDPOINT",
    "https://kxbwmqov6jgg3daaamb744ycu4.appsync-api.us-east-1.amazonaws.com/graphql",
).strip()
GOODREADS_GRAPHQL_API_KEY = os.environ.get(
    "GOODREADS_GRAPHQL_API_KEY", "da2-xpgsdydkbregjhpr6ejzqdhuwy",
).strip()
GOODREADS_BOOK_QUERY = """
query ExternalBookRatings($legacyBookId: Int!) {
  getBookByLegacyId(legacyId: $legacyBookId) {
    id legacyId title titleComplete webUrl
    details { isbn isbn13 }
    primaryContributorEdge { node { id name } }
    work {
      legacyId
      details { originalTitle webUrl }
      stats { averageRating ratingsCount ratingsCountDist textReviewsCount }
      bestBook {
        legacyId title titleComplete webUrl
        primaryContributorEdge { node { id name } }
      }
    }
  }
}
"""
GOODREADS_SEARCH_QUERY = """
query getSearchSuggestions($searchQuery: String!) {
  getSearchSuggestions(query: $searchQuery) {
    edges {
      ... on SearchBookEdge {
        node {
          id
          title
          primaryContributorEdge { node { name isGrAuthor } }
          webUrl
        }
      }
    }
  }
}
"""
GOODREADS_AUTHOR_WORKS_QUERY = """
query ExternalAuthorWorks(
  $input: GetWorksByContributorInput!,
  $pagination: PaginationInput
) {
  getWorksByContributor(
    getWorksByContributorInput: $input,
    pagination: $pagination
  ) {
    edges {
      node {
        id
        stats { averageRating ratingsCount ratingsCountDist textReviewsCount }
        bestBook {
          legacyId title titleComplete webUrl
          primaryContributorEdge { node { id name } }
        }
      }
    }
  }
}
"""


def _goodreads_legacy_id(lookup: BookLookup) -> tuple[int | None, str | None]:
    if lookup.goodreads_id:
        match = re.search(r"\d+", str(lookup.goodreads_id))
        if match:
            return int(match.group(0)), "goodreads_id"

    normalized_isbn = normalize_isbn(lookup.isbn)
    if not normalized_isbn:
        return None, None

    temporarily_blocked = False
    for url in (
        f"https://www.goodreads.com/book/isbn/{normalized_isbn}",
        f"https://www.goodreads.com/book/isbn?isbn={normalized_isbn}",
    ):
        response = requests.get(
            url,
            headers=GOODREADS_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code == 202:
            temporarily_blocked = True
            continue
        if response.status_code == 404:
            continue
        response.raise_for_status()
        location = str(response.headers.get("Location") or response.headers.get("location") or "")
        match = re.search(r"/book/show/(\d+)", location)
        if match:
            return int(match.group(1)), "isbn"
        match = re.search(r"/book/show/(\d+)", str(response.url))
        if match:
            return int(match.group(1)), "isbn"

    if temporarily_blocked:
        raise ProviderUnavailable("Goodreads temporarily blocked the ISBN lookup")
    return None, None


def _goodreads_graphql_data(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if not GOODREADS_GRAPHQL_ENDPOINT or not GOODREADS_GRAPHQL_API_KEY:
        raise ProviderUnavailable("Goodreads GraphQL configuration is unavailable")
    response = requests.post(
        GOODREADS_GRAPHQL_ENDPOINT,
        json={"query": query, "variables": variables},
        headers={
            "x-api-key": GOODREADS_GRAPHQL_API_KEY,
            "content-type": "application/json",
            "origin": "https://www.goodreads.com",
            "referer": "https://www.goodreads.com/",
            "User-Agent": GOODREADS_HEADERS["User-Agent"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code in {401, 403}:
        raise ProviderUnavailable("Goodreads public data endpoint rejected the request")
    response.raise_for_status()
    try:
        payload = response.json() or {}
    except ValueError as exc:
        raise RuntimeError("Goodreads returned invalid JSON") from exc
    if payload.get("errors") and not payload.get("data"):
        raise ProviderUnavailable("Goodreads public data endpoint returned an error")
    data = payload.get("data") or {}
    return data if isinstance(data, dict) else {}


def _goodreads_graphql_search_candidates(
    lookup: BookLookup,
) -> list[tuple[int, float, str]]:
    ranked: list[tuple[float, int, str]] = []
    seen: set[int] = set()
    for title, authors, _variant_match in lookup.identity_variants:
        query = " ".join((title, *authors[:1])).strip()
        if not query:
            continue
        data = _goodreads_graphql_data(
            GOODREADS_SEARCH_QUERY,
            {"searchQuery": query},
        )
        edges = ((data.get("getSearchSuggestions") or {}).get("edges")) or []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            web_url = str(node.get("webUrl") or "")
            legacy_match = re.search(r"/book/show/(\d+)", web_url)
            if not legacy_match:
                continue
            legacy_id = int(legacy_match.group(1))
            if legacy_id in seen:
                continue
            contributor = ((node.get("primaryContributorEdge") or {}).get("node") or {})
            candidate_authors = [str(contributor.get("name"))] if contributor.get("name") else []
            confidence, matched_by = candidate_confidence(
                lookup,
                str(node.get("title") or ""),
                candidate_authors,
                [],
                None,
            )
            if confidence >= 0.62:
                seen.add(legacy_id)
                ranked.append((confidence, legacy_id, matched_by))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        (legacy_id, confidence, matched_by)
        for confidence, legacy_id, matched_by in ranked
    ]


def _latin_title_markers(title: str | None) -> tuple[str, ...]:
    stopwords = {
        "book", "edition", "history", "story", "volume", "part",
        "true", "new", "how", "and", "the", "for", "from",
    }
    return tuple(dict.fromkeys(
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]{3,}", str(title or ""))
        if token.casefold() not in stopwords
    ))


def _title_acronyms(title: str | None) -> set[str]:
    words = re.findall(r"[A-Za-z0-9]+", str(title or "").casefold())
    result: set[str] = set()
    for size in range(2, min(5, len(words)) + 1):
        for index in range(0, len(words) - size + 1):
            result.add("".join(word[0] for word in words[index:index + size] if word))
    return result


def _goodreads_author_work_fallback(
    lookup: BookLookup, book: dict[str, Any],
) -> ExternalRatingResult | None:
    contributor = ((book.get("primaryContributorEdge") or {}).get("node") or {})
    contributor_id = str(contributor.get("id") or "").strip()
    if not contributor_id:
        return None
    markers = _latin_title_markers(lookup.title)
    if not markers:
        return None

    data = _goodreads_graphql_data(
        GOODREADS_AUTHOR_WORKS_QUERY,
        {
            "input": {"id": contributor_id},
            "pagination": {"limit": 20},
        },
    )
    edges = ((data.get("getWorksByContributor") or {}).get("edges")) or []
    ranked: list[tuple[float, float, int, dict[str, Any]]] = []
    for edge in edges:
        node = (edge or {}).get("node") or {}
        best_book = node.get("bestBook") or {}
        title = str(best_book.get("titleComplete") or best_book.get("title") or "").strip()
        if not title:
            continue
        normalized_title = normalize_text(title)
        title_tokens = set(normalized_title.split())
        acronyms = _title_acronyms(title)
        matches = sum(1 for marker in markers if marker in title_tokens or marker in acronyms)
        marker_score = matches / len(markers)
        if marker_score <= 0:
            continue
        stats = node.get("stats") or {}
        rating = _safe_float(stats.get("averageRating"))
        ratings_count = _safe_int(stats.get("ratingsCount"))
        reviews_count = _safe_int(stats.get("textReviewsCount"))
        if not any(value for value in (rating, ratings_count, reviews_count)):
            continue
        local_title_score = SequenceMatcher(
            None, normalize_text(lookup.title), normalized_title,
        ).ratio()
        ranked.append((
            marker_score,
            local_title_score,
            ratings_count or 0,
            node,
        ))

    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    top_marker, top_title, _top_count, node = ranked[0]
    if top_marker < 0.8:
        return None
    if len(ranked) > 1:
        second_marker, second_title, _second_count, _second_node = ranked[1]
        if second_marker == top_marker and abs(top_title - second_title) < 0.08:
            return None

    best_book = node.get("bestBook") or {}
    stats = node.get("stats") or {}
    author = ((best_book.get("primaryContributorEdge") or {}).get("node") or {})
    raw_distribution = stats.get("ratingsCountDist") or []
    distribution = {
        str(index): _safe_int(count) or 0
        for index, count in enumerate(raw_distribution, start=1)
        if index <= 5
    } or None
    confidence = min(0.95, 0.82 + top_marker * 0.1 + top_title * 0.05)
    return ExternalRatingResult(
        source="goodreads",
        source_id=str(best_book.get("legacyId") or node.get("id") or ""),
        source_url=str(best_book.get("webUrl") or "https://www.goodreads.com/"),
        matched_title=str(best_book.get("titleComplete") or best_book.get("title") or lookup.title),
        matched_authors=[str(author.get("name"))] if author.get("name") else [],
        matched_by="author_work_marker",
        match_confidence=confidence,
        rating=_safe_float(stats.get("averageRating")),
        ratings_count=_safe_int(stats.get("ratingsCount")),
        reviews_count=_safe_int(stats.get("textReviewsCount")),
        ratings_distribution=distribution,
    )


def _goodreads_graphql_result(
    lookup: BookLookup,
    legacy_id: int,
    matched_by: str,
    match_confidence: float | None = None,
) -> ExternalRatingResult:
    data = _goodreads_graphql_data(
        GOODREADS_BOOK_QUERY,
        {"legacyBookId": int(legacy_id)},
    )
    book = data.get("getBookByLegacyId") or {}
    if not isinstance(book, dict) or not book:
        raise ProviderNotFound("Goodreads book was not found")

    details = book.get("details") or {}
    page_isbns = {
        value for value in (
            normalize_isbn(details.get("isbn13")),
            normalize_isbn(details.get("isbn")),
        ) if value
    }
    expected_isbn = normalize_isbn(lookup.isbn)
    if matched_by == "isbn" and expected_isbn and page_isbns and expected_isbn not in page_isbns:
        raise ProviderNotFound("Goodreads redirected the ISBN to a different edition")

    work = book.get("work") or {}
    work_details = work.get("details") or {}
    best_book = work.get("bestBook") or {}
    contributor = ((best_book.get("primaryContributorEdge") or {}).get("node") or {})
    if not contributor:
        contributor = ((book.get("primaryContributorEdge") or {}).get("node") or {})
    authors = [str(contributor.get("name"))] if contributor.get("name") else []
    canonical_title = str(
        work_details.get("originalTitle")
        or best_book.get("titleComplete")
        or best_book.get("title")
        or book.get("titleComplete")
        or book.get("title")
        or lookup.title
    ).strip()

    stats = work.get("stats") or {}
    rating = _safe_float(stats.get("averageRating"))
    ratings_count = _safe_int(stats.get("ratingsCount"))
    reviews_count = _safe_int(stats.get("textReviewsCount"))
    raw_distribution = stats.get("ratingsCountDist") or []
    distribution = {
        str(index): _safe_int(count) or 0
        for index, count in enumerate(raw_distribution, start=1)
        if index <= 5
    } or None
    if not any(value for value in (rating, ratings_count, reviews_count)):
        fallback = _goodreads_author_work_fallback(lookup, book)
        if fallback is not None:
            return fallback
        raise ProviderNotFound("Goodreads has no rating data")

    if matched_by in {"isbn", "goodreads_id"}:
        confidence = 1.0
    elif match_confidence is not None:
        confidence = float(match_confidence)
    else:
        confidence, matched_by = candidate_confidence(
            lookup, canonical_title, authors, page_isbns, None,
        )
    if confidence < 0.62:
        raise ProviderNotFound("No sufficiently confident Goodreads match was found")

    source_id = str(work.get("legacyId") or book.get("legacyId") or legacy_id)
    source_url = str(
        work_details.get("webUrl")
        or best_book.get("webUrl")
        or book.get("webUrl")
        or f"https://www.goodreads.com/book/show/{legacy_id}"
    )
    return ExternalRatingResult(
        source="goodreads",
        source_id=source_id,
        source_url=source_url,
        matched_title=canonical_title,
        matched_authors=authors,
        matched_by=matched_by,
        match_confidence=confidence,
        rating=rating,
        ratings_count=ratings_count,
        reviews_count=reviews_count,
        ratings_distribution=distribution,
    )


def _goodreads_search_candidates(
    html: str, lookup: BookLookup,
) -> list[tuple[int, str, float, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    ranked: list[tuple[float, int, str, str]] = []
    seen: set[int] = set()
    links = soup.select('a.bookTitle[href*="/book/show/"], a[href*="/book/show/"]')
    for link in links:
        title = link.get_text(" ", strip=True)
        href = str(link.get("href") or "")
        match = re.search(r"/book/show/(\d+)", href)
        if not title or not match:
            continue
        legacy_id = int(match.group(1))
        if legacy_id in seen:
            continue
        container = link.find_parent("tr") or link.find_parent("div") or link.parent
        authors: list[str] = []
        if container is not None:
            for author_node in container.select("a.authorName, [itemprop='author'] [itemprop='name']"):
                author = author_node.get_text(" ", strip=True)
                if author and author not in authors:
                    authors.append(author)
        text = container.get_text(" ", strip=True) if container is not None else ""
        year_match = re.search(r"published\s+(\d{4})", text, re.IGNORECASE)
        confidence, matched_by = candidate_confidence(
            lookup,
            title,
            authors,
            [],
            year_match.group(1) if year_match else None,
        )
        if confidence >= 0.62:
            seen.add(legacy_id)
            ranked.append((
                confidence,
                legacy_id,
                urljoin("https://www.goodreads.com/", href.split("?", 1)[0]),
                matched_by,
            ))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        (legacy_id, url, confidence, matched_by)
        for confidence, legacy_id, url, matched_by in ranked
    ]


def _apollo_ref(state: dict[str, Any], value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    ref = value.get("__ref")
    resolved = state.get(ref) if isinstance(ref, str) else None
    return resolved if isinstance(resolved, dict) else {}


def _goodreads_state(html: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    node = soup.select_one("script#__NEXT_DATA__")
    if node is None:
        return {}, {}, {}
    try:
        payload = json.loads(node.string or node.get_text() or "{}")
    except (TypeError, ValueError):
        return {}, {}, {}
    state = (((payload.get("props") or {}).get("pageProps") or {}).get("apolloState") or {})
    if not isinstance(state, dict):
        return {}, {}, {}
    root = state.get("ROOT_QUERY") or {}
    book_ref = next((
        value for key, value in root.items()
        if str(key).startswith("getBookByLegacyId(") and isinstance(value, dict)
    ), {})
    book = _apollo_ref(state, book_ref)
    work = _apollo_ref(state, book.get("work"))
    return state, book, work


def _goodreads_parse(html: str, final_url: str, lookup: BookLookup, direct_match: str | None):
    state, book, work = _goodreads_state(html)
    if book:
        details = book.get("details") or {}
        page_isbns = {
            value for value in (
                normalize_isbn(details.get("isbn13")),
                normalize_isbn(details.get("isbn")),
            ) if value
        }
        expected_isbn = normalize_isbn(lookup.isbn)
        if expected_isbn and page_isbns and expected_isbn not in page_isbns:
            raise ProviderNotFound("Goodreads redirected the ISBN to a different edition")

        contributor = _apollo_ref(state, (book.get("primaryContributorEdge") or {}).get("node"))
        authors = [str(contributor.get("name"))] if contributor.get("name") else []
        original_title = str(((work.get("details") or {}).get("originalTitle")) or "").strip()
        best_book = _apollo_ref(state, work.get("bestBook"))
        canonical_title = original_title or str(best_book.get("titleComplete") or best_book.get("title") or "").strip()
        canonical_title = canonical_title or str(book.get("titleComplete") or book.get("title") or lookup.title)

        stats = work.get("stats") or {}
        rating = _safe_float(stats.get("averageRating"))
        ratings_count = _safe_int(stats.get("ratingsCount"))
        reviews_count = _safe_int(stats.get("textReviewsCount"))
        raw_distribution = stats.get("ratingsCountDist") or []
        distribution = {
            str(index): _safe_int(count) or 0
            for index, count in enumerate(raw_distribution, start=1)
            if index <= 5
        } or None

        popularity_count = 0
        root = state.get("ROOT_QUERY") or {}
        for key, value in root.items():
            if str(key).startswith("getSocialSignals(") and isinstance(value, list):
                popularity_count += sum(_safe_int(signal.get("count")) or 0 for signal in value if isinstance(signal, dict))
        popularity = popularity_count or None

        if rating is None and not ratings_count and not reviews_count and not popularity:
            raise ProviderNotFound("Goodreads has no rating or popularity data")

        if direct_match:
            confidence, matched_by = 1.0, direct_match
        else:
            confidence, matched_by = candidate_confidence(
                lookup, canonical_title, authors,
                page_isbns, None,
            )
            if confidence < 0.62:
                raise ProviderNotFound("No sufficiently confident Goodreads match was found")

        source_id = str(work.get("legacyId") or book.get("legacyId") or "")
        source_url = str((work.get("details") or {}).get("webUrl") or book.get("webUrl") or final_url)
        return ExternalRatingResult(
            source="goodreads",
            source_id=source_id,
            source_url=source_url,
            matched_title=canonical_title,
            matched_authors=authors,
            matched_by=matched_by,
            match_confidence=confidence,
            rating=rating,
            ratings_count=ratings_count,
            reviews_count=reviews_count,
            popularity_count=popularity,
            ratings_distribution=distribution,
        )

    soup = BeautifulSoup(html or "", "html.parser")
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.string or node.get_text() or "{}")
        except (TypeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("@type") != "Book":
                continue
            authors = []
            raw_authors = candidate.get("author") or []
            raw_authors = raw_authors if isinstance(raw_authors, list) else [raw_authors]
            for author in raw_authors:
                name = author.get("name") if isinstance(author, dict) else author
                if name and str(name) not in authors:
                    authors.append(str(name))
            aggregate = candidate.get("aggregateRating") or {}
            rating = _safe_float(aggregate.get("ratingValue"))
            ratings_count = _safe_int(aggregate.get("ratingCount"))
            reviews_count = _safe_int(aggregate.get("reviewCount"))
            if rating is None and not ratings_count and not reviews_count:
                continue
            page_isbn = normalize_isbn(candidate.get("isbn"))
            confidence, matched_by = candidate_confidence(
                lookup, str(candidate.get("name") or ""), authors,
                [page_isbn] if page_isbn else [], None,
            )
            if direct_match:
                confidence, matched_by = 1.0, direct_match
            if confidence < 0.62:
                continue
            match = re.search(r"/book/show/(\d+)", final_url)
            return ExternalRatingResult(
                source="goodreads",
                source_id=match.group(1) if match else "",
                source_url=final_url,
                matched_title=str(candidate.get("name") or lookup.title),
                matched_authors=authors,
                matched_by=matched_by,
                match_confidence=confidence,
                rating=rating,
                ratings_count=ratings_count,
                reviews_count=reviews_count,
            )
    raise ProviderNotFound("Goodreads book data was not found")


def fetch_goodreads(lookup: BookLookup) -> ExternalRatingResult:
    legacy_id, direct_match = _goodreads_legacy_id(lookup)
    if legacy_id is not None and direct_match is not None:
        try:
            return _goodreads_graphql_result(lookup, legacy_id, direct_match)
        except ProviderNotFound:
            pass
        except ProviderUnavailable as exc:
            log.debug("Goodreads GraphQL lookup unavailable; falling back to search: %s", exc)

    try:
        search_results: list[ExternalRatingResult] = []
        for candidate_id, confidence, candidate_match in _goodreads_graphql_search_candidates(lookup):
            try:
                search_results.append(_goodreads_graphql_result(
                    lookup,
                    candidate_id,
                    candidate_match,
                    confidence,
                ))
            except ProviderNotFound:
                continue
        if search_results:
            best_confidence = max(result.match_confidence for result in search_results)
            close_matches = [
                result for result in search_results
                if result.match_confidence >= best_confidence - 0.08
            ]
            established_matches = [
                result for result in close_matches
                if (result.ratings_count or 0) >= 10
            ]
            pool = established_matches or close_matches
            return max(
                pool,
                key=lambda result: (
                    result.ratings_count or 0,
                    result.reviews_count or 0,
                    result.match_confidence,
                ),
            )
    except ProviderUnavailable as exc:
        log.debug("Goodreads GraphQL search unavailable; falling back to HTML: %s", exc)

    targets: list[tuple[str, str | None]] = []
    if legacy_id is None and lookup.goodreads_id:
        targets.append((f"https://www.goodreads.com/book/show/{lookup.goodreads_id}", "goodreads_id"))
    if legacy_id is None and lookup.isbn:
        targets.extend((
            (f"https://www.goodreads.com/book/isbn/{lookup.isbn}", "isbn"),
            (f"https://www.goodreads.com/book/isbn?isbn={lookup.isbn}", "isbn"),
        ))
    for title, authors, _matched_by in lookup.identity_variants:
        query = " ".join((title, *authors[:1])).strip()
        if query:
            targets.append((f"https://www.goodreads.com/search?q={quote_plus(query)}", None))

    last_not_found: ProviderNotFound | None = None
    temporarily_blocked = False
    for url, direct_match in dict.fromkeys(targets):
        response = requests.get(
            url, headers=GOODREADS_HEADERS, timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code == 202:
            temporarily_blocked = True
            continue
        if response.status_code == 404:
            last_not_found = ProviderNotFound("Goodreads book was not found")
            continue
        response.raise_for_status()
        final_url = str(response.url)
        html = response.text
        if "/book/show/" not in final_url:
            candidates = _goodreads_search_candidates(html, lookup)
            graphql_unavailable = False
            for candidate_id, candidate_url, confidence, candidate_match in candidates:
                try:
                    return _goodreads_graphql_result(
                        lookup,
                        candidate_id,
                        candidate_match,
                        confidence,
                    )
                except ProviderNotFound as exc:
                    last_not_found = exc
                    continue
                except ProviderUnavailable as exc:
                    log.debug(
                        "Goodreads search GraphQL lookup unavailable; falling back to HTML: %s",
                        exc,
                    )
                    graphql_unavailable = True
                    final_url = candidate_url
                    break
            else:
                if not candidates:
                    last_not_found = ProviderNotFound("Goodreads search returned no book")
                continue

            if not graphql_unavailable:
                continue
            response = requests.get(
                final_url, headers=GOODREADS_HEADERS, timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            if response.status_code == 202:
                temporarily_blocked = True
                continue
            response.raise_for_status()
            html = response.text
            final_url = str(response.url)
        try:
            return _goodreads_parse(html, final_url, lookup, direct_match)
        except ProviderNotFound as exc:
            last_not_found = exc
    if temporarily_blocked:
        raise ProviderUnavailable("Goodreads temporarily blocked the automated request")
    raise last_not_found or ProviderNotFound("No Goodreads match was found")


HARDCOVER_SEARCH_QUERY = """
query SearchBooks($query: String!) {
  search(query: $query, query_type: "Book", per_page: 20) { results }
}
"""

HARDCOVER_BOOK_QUERY = """
query ExternalBookRatings($id: Int!) {
  books(where: {id: {_eq: $id}}, limit: 1) {
    id slug title release_year rating ratings_count ratings_distribution
    reviews_count users_count users_read_count
    editions(limit: 30) {
      isbn_13 isbn_10
      contributions { author { name } }
    }
  }
}
"""


def _hardcover_request(
    query: str, variables: dict[str, Any], tokens: Iterable[str],
) -> dict[str, Any]:
    clean_tokens = []
    for token in tokens:
        cleaned = str(token or "").replace("Bearer ", "", 1).strip()
        if cleaned and cleaned not in clean_tokens:
            clean_tokens.append(cleaned)
    if not clean_tokens:
        raise ProviderUnavailable("Hardcover API token is not configured")

    last_error: Exception | None = None
    for token in clean_tokens:
        try:
            response = requests.post(
                "https://api.hardcover.app/v1/graphql",
                json={"query": query, "variables": variables},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": constants.USER_AGENT,
                },
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 401:
                last_error = ProviderUnavailable("Hardcover API token was rejected")
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError("Hardcover GraphQL request failed")
            return payload.get("data") or {}
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
    if isinstance(last_error, ProviderUnavailable):
        raise last_error
    raise RuntimeError("Hardcover request failed") from last_error


def _hardcover_search_id(lookup: BookLookup, tokens: Iterable[str]) -> tuple[int, float, str]:
    def unpack(hit: dict[str, Any]):
        document = hit.get("document") or {}
        isbns = []
        for key in ("isbns", "isbn", "isbn_10", "isbn_13"):
            value = document.get(key)
            isbns.extend(value if isinstance(value, list) else [value])
        return (
            str(document.get("title") or ""),
            list(document.get("author_names") or []),
            isbns,
            document.get("release_year") or document.get("release_date"),
        )

    queries = []
    if lookup.isbn:
        queries.append(lookup.isbn)
    for title, authors, _matched_by in lookup.identity_variants:
        queries.append(" ".join((title, *authors[:2])).strip())
    last_not_found: ProviderNotFound | None = None
    for query in dict.fromkeys(value for value in queries if value):
        data = _hardcover_request(HARDCOVER_SEARCH_QUERY, {"query": query}, tokens)
        raw = ((data.get("search") or {}).get("results")) or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise RuntimeError("Hardcover search returned invalid JSON") from exc
        hits = raw.get("hits") if isinstance(raw, dict) else []
        try:
            hit, confidence, matched_by = _best_candidate(lookup, hits or [], unpack)
        except ProviderNotFound as exc:
            last_not_found = exc
            continue
        document = hit.get("document") or {}
        book_id = _safe_int(document.get("id"))
        if book_id is None:
            last_not_found = ProviderNotFound("Hardcover match has no book id")
            continue
        return book_id, confidence, matched_by

    raise last_not_found or ProviderNotFound("No Hardcover match was found")


def fetch_hardcover(lookup: BookLookup, tokens: Iterable[str]) -> ExternalRatingResult:
    if lookup.hardcover_id is not None:
        book_id, confidence, matched_by = lookup.hardcover_id, 1.0, "hardcover_id"
    else:
        book_id, confidence, matched_by = _hardcover_search_id(lookup, tokens)

    data = _hardcover_request(HARDCOVER_BOOK_QUERY, {"id": int(book_id)}, tokens)
    books = data.get("books") or []
    if not books:
        raise ProviderNotFound("Hardcover book was not found")
    book = books[0]
    authors: list[str] = []
    for edition in book.get("editions") or []:
        for contribution in edition.get("contributions") or []:
            name = ((contribution.get("author") or {}).get("name"))
            if name and name not in authors:
                authors.append(str(name))

    rating = _safe_float(book.get("rating"))
    ratings_count = _safe_int(book.get("ratings_count"))
    reviews_count = _safe_int(book.get("reviews_count"))
    popularity_count = _safe_int(book.get("users_read_count"))
    if popularity_count is None:
        popularity_count = _safe_int(book.get("users_count"))
    if not any(value for value in (rating, ratings_count, reviews_count, popularity_count)):
        raise ProviderNotFound("Hardcover has no rating or popularity data")

    slug = str(book.get("slug") or lookup.hardcover_slug or "")
    return ExternalRatingResult(
        source="hardcover",
        source_id=str(book.get("id") or book_id),
        source_url=f"https://hardcover.app/books/{slug}" if slug else "https://hardcover.app/",
        matched_title=str(book.get("title") or lookup.title),
        matched_authors=authors,
        matched_by=matched_by,
        match_confidence=confidence,
        rating=rating,
        ratings_count=ratings_count,
        reviews_count=reviews_count,
        popularity_count=popularity_count,
        ratings_distribution=_distribution(book.get("ratings_distribution")),
    )


def _title_author_query(
    title: str, authors: Iterable[str],
    title_field: str, author_field: str, separator: str = " AND ",
) -> str:
    clean_title = str(title or "").replace('"', "")
    parts = [f'{title_field}:"{clean_title}"']
    authors = tuple(authors)
    if authors:
        parts.append(f'{author_field}:"{authors[0].replace(chr(34), "")}"')
    return separator.join(parts)


def _google_items(
    lookup: BookLookup, api_key: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    params: dict[str, Any] = {"projection": "full"}
    resolved_api_key = str(api_key or os.environ.get("GOOGLE_BOOKS_API_KEY", "")).strip()
    if resolved_api_key:
        params["key"] = resolved_api_key

    if lookup.google_id:
        response = requests.get(
            f"https://www.googleapis.com/books/v1/volumes/{lookup.google_id}",
            params=params,
            headers={"User-Agent": constants.USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return [response.json() or {}], "google_id"

    queries = []
    if lookup.isbn:
        queries.append(f"isbn:{lookup.isbn}")
    for title, authors, _matched_by in lookup.identity_variants:
        queries.append(_title_author_query(
            title, authors, "intitle", "inauthor", separator=" ",
        ))
    for query in dict.fromkeys(value for value in queries if value):
        response = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={**params, "q": query, "maxResults": 10, "printType": "books"},
            headers={"User-Agent": constants.USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        items = (response.json() or {}).get("items") or []
        if items:
            return items, None
    return [], None


def fetch_google_books(
    lookup: BookLookup, api_key: str | None = None,
) -> ExternalRatingResult:
    items, direct_match = _google_items(lookup, api_key)

    def unpack(item: dict[str, Any]):
        info = item.get("volumeInfo") or {}
        isbns = [entry.get("identifier") for entry in info.get("industryIdentifiers") or []]
        return (
            str(info.get("title") or ""),
            list(info.get("authors") or []),
            isbns,
            str(info.get("publishedDate") or "")[:4],
        )

    if direct_match and items:
        item, confidence, matched_by = items[0], 1.0, direct_match
    else:
        item, confidence, matched_by = _best_candidate(lookup, items, unpack)
    info = item.get("volumeInfo") or {}
    rating = _safe_float(info.get("averageRating"))
    ratings_count = _safe_int(info.get("ratingsCount"))
    if rating is None and not ratings_count:
        raise ProviderNotFound("Google Books has no rating data")
    volume_id = str(item.get("id") or lookup.google_id or "")
    source_url = str(info.get("infoLink") or "")
    if not source_url.startswith(("http://", "https://")):
        source_url = f"https://books.google.com/books?id={volume_id}"
    return ExternalRatingResult(
        source="google_books",
        source_id=volume_id,
        source_url=source_url,
        matched_title=str(info.get("title") or lookup.title),
        matched_authors=list(info.get("authors") or []),
        matched_by=matched_by,
        match_confidence=confidence,
        rating=rating,
        ratings_count=ratings_count,
    )


OPEN_LIBRARY_FIELDS = ",".join((
    "key", "title", "author_name", "isbn", "first_publish_year",
    "ratings_average", "ratings_count", "ratings_count_1", "ratings_count_2",
    "ratings_count_3", "ratings_count_4", "ratings_count_5",
    "want_to_read_count", "currently_reading_count", "already_read_count",
    "readinglog_count",
))


def _open_library_docs(lookup: BookLookup) -> tuple[list[dict[str, Any]], str | None]:
    queries = []
    if lookup.open_library_id:
        key = lookup.open_library_id
        if not key.startswith("/"):
            key = f"/works/{key}"
        queries.append((f"key:{key}", "open_library_id"))
    if lookup.isbn:
        queries.append((f"isbn:{lookup.isbn}", None))
    for title, authors, _matched_by in lookup.identity_variants:
        queries.append((
            _title_author_query(title, authors, "title", "author"),
            None,
        ))

    for query, direct_match in dict.fromkeys(queries):
        response = requests.get(
            "https://openlibrary.org/search.json",
            params={"q": query, "fields": OPEN_LIBRARY_FIELDS, "limit": 10},
            headers={
                "User-Agent": f"{constants.USER_AGENT} (external book ratings)",
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        docs = (response.json() or {}).get("docs") or []
        if docs:
            return docs, direct_match
    return [], None


def _open_library_work_stats(
    key: str,
) -> tuple[float | None, int | None, int | None, dict[str, int] | None]:
    if not key.startswith("/works/"):
        return None, None, None, None

    headers = {
        "User-Agent": f"{constants.USER_AGENT} (external book ratings)",
        "Accept": "application/json",
    }
    urls = {
        "ratings": f"https://openlibrary.org{key}/ratings.json",
        "bookshelves": f"https://openlibrary.org{key}/bookshelves.json",
    }

    def load(url: str) -> dict[str, Any]:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    payloads: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="open-library-stats") as pool:
        futures = {pool.submit(load, url): name for name, url in urls.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                payloads[name] = future.result()
            except (requests.RequestException, ValueError):
                payloads[name] = {}

    ratings = payloads.get("ratings") or {}
    summary = ratings.get("summary") or {}
    count = _safe_int(summary.get("count"))
    average = _safe_float(summary.get("average")) if count else None
    raw_counts = ratings.get("counts") or {}
    distribution = {
        str(star): _safe_int(raw_counts.get(str(star), raw_counts.get(star))) or 0
        for star in range(1, 6)
    }
    if not any(distribution.values()):
        distribution = None

    bookshelf_counts = (payloads.get("bookshelves") or {}).get("counts") or {}
    popularity = sum(
        _safe_int(bookshelf_counts.get(name)) or 0
        for name in ("want_to_read", "currently_reading", "already_read")
    )
    return average, count, popularity or None, distribution


def fetch_open_library(lookup: BookLookup) -> ExternalRatingResult:
    docs, direct_match = _open_library_docs(lookup)

    def unpack(doc: dict[str, Any]):
        return (
            str(doc.get("title") or ""),
            list(doc.get("author_name") or []),
            list(doc.get("isbn") or []),
            doc.get("first_publish_year"),
        )

    if direct_match and docs:
        doc, confidence, matched_by = docs[0], 1.0, direct_match
    else:
        doc, confidence, matched_by = _best_candidate(lookup, docs, unpack)
    rating = _safe_float(doc.get("ratings_average"))
    ratings_count = _safe_int(doc.get("ratings_count"))
    popularity_count = _safe_int(doc.get("readinglog_count"))
    key = str(doc.get("key") or "")
    distribution = {
        str(star): _safe_int(doc.get(f"ratings_count_{star}")) or 0
        for star in range(1, 6)
    }
    if not any(distribution.values()):
        distribution = None

    if (rating is None or not ratings_count or not popularity_count or distribution is None) and key:
        work_rating, work_count, work_popularity, work_distribution = _open_library_work_stats(key)
        rating = rating if rating is not None else work_rating
        ratings_count = ratings_count or work_count
        popularity_count = popularity_count or work_popularity
        distribution = distribution or work_distribution

    if rating is None and not ratings_count and not popularity_count:
        raise ProviderNotFound("Open Library has no rating or reading-log data")
    ratings_count = ratings_count or None
    popularity_count = popularity_count or None
    return ExternalRatingResult(
        source="open_library",
        source_id=key.rsplit("/", 1)[-1],
        source_url=f"https://openlibrary.org{key}" if key.startswith("/") else "https://openlibrary.org/",
        matched_title=str(doc.get("title") or lookup.title),
        matched_authors=list(doc.get("author_name") or []),
        matched_by=matched_by,
        match_confidence=confidence,
        rating=rating,
        ratings_count=ratings_count,
        popularity_count=popularity_count,
        ratings_distribution=distribution,
    )


def _run_provider(source: str, call: Callable[[], ExternalRatingResult]) -> ProviderOutcome:
    try:
        return ProviderOutcome(source=source, status="ok", result=call())
    except ProviderUnavailable as exc:
        return ProviderOutcome(source=source, status="unavailable", error=str(exc))
    except ProviderNotFound as exc:
        return ProviderOutcome(source=source, status="not_found", error=str(exc))
    except requests.Timeout:
        return ProviderOutcome(source=source, status="error", error="Provider request timed out")
    except requests.RequestException:
        return ProviderOutcome(source=source, status="error", error="Provider request failed")
    except Exception:
        log.warning("External ratings provider %s failed", source, exc_info=True)
        return ProviderOutcome(source=source, status="error", error="Provider response could not be processed")


def fetch_external_ratings(
    lookup: BookLookup,
    hardcover_tokens: Iterable[str] = (),
    google_books_api_key: str | None = None,
) -> list[ProviderOutcome]:
    outcomes: dict[str, ProviderOutcome] = {}
    goodreads_outcome = _run_provider("goodreads", lambda: fetch_goodreads(lookup))
    outcomes["goodreads"] = goodreads_outcome

    enriched_lookup = lookup
    if goodreads_outcome.result is not None:
        enriched_lookup = lookup.with_canonical(
            goodreads_outcome.result.matched_title,
            goodreads_outcome.result.matched_authors,
        )

    providers: dict[str, Callable[[], ExternalRatingResult]] = {
        "hardcover": lambda: fetch_hardcover(enriched_lookup, hardcover_tokens),
        "google_books": lambda: fetch_google_books(enriched_lookup, google_books_api_key),
        "open_library": lambda: fetch_open_library(enriched_lookup),
    }
    with ThreadPoolExecutor(max_workers=len(providers), thread_name_prefix="external-ratings") as pool:
        futures = {
            pool.submit(_run_provider, source, call): source
            for source, call in providers.items()
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                outcomes[source] = future.result()
            except Exception:
                log.warning("External ratings worker %s failed", source, exc_info=True)
                outcomes[source] = ProviderOutcome(
                    source=source, status="error", error="Provider worker failed",
                )
    return [outcomes[source] for source in SOURCE_ORDER]
