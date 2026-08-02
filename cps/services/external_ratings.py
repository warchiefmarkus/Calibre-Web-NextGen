# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""External book rating and popularity providers.

The service is deliberately independent from Flask and SQLAlchemy. API routes
pass it a plain lookup object and persist the returned provider outcomes in
app.db. Providers return aggregate data only; user review text is not copied.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Callable, Iterable

import requests

from .. import constants, logger

log = logger.create()

REQUEST_TIMEOUT = 8
SOURCE_ORDER = ("hardcover", "google_books", "open_library")


@dataclass(frozen=True)
class BookLookup:
    book_id: int
    title: str
    authors: tuple[str, ...] = ()
    isbn: str | None = None
    publication_year: int | None = None
    hardcover_id: int | None = None
    hardcover_slug: str | None = None
    google_id: str | None = None
    open_library_id: str | None = None

    @property
    def identity_hash(self) -> str:
        body = json.dumps({
            "title": normalize_text(self.title),
            "authors": [normalize_text(author) for author in self.authors],
            "isbn": normalize_isbn(self.isbn),
            "publication_year": self.publication_year,
            "hardcover_id": self.hardcover_id,
            "hardcover_slug": self.hardcover_slug or "",
            "google_id": self.google_id or "",
            "open_library_id": self.open_library_id or "",
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


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

    title_score = SequenceMatcher(
        None, normalize_text(lookup.title), normalize_text(title),
    ).ratio()
    author_score = _author_similarity(lookup.authors, authors)
    score = title_score * 0.72 + author_score * 0.25
    matched_by = "title_author"

    year = _safe_int(publication_year)
    if lookup.publication_year and year:
        delta = abs(lookup.publication_year - year)
        if delta == 0:
            score += 0.03
        elif delta == 1:
            score += 0.015

    return min(score, 1.0), matched_by


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
    queries.append(" ".join((lookup.title, *lookup.authors[:2])).strip())
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
    lookup: BookLookup, title_field: str, author_field: str, separator: str = " AND ",
) -> str:
    title = lookup.title.replace('"', "")
    parts = [f'{title_field}:"{title}"']
    if lookup.authors:
        parts.append(f'{author_field}:"{lookup.authors[0].replace(chr(34), "")}"')
    return separator.join(parts)


def _google_items(lookup: BookLookup) -> tuple[list[dict[str, Any]], str | None]:
    params: dict[str, Any] = {"projection": "full"}
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
    if api_key:
        params["key"] = api_key

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
    queries.append(_title_author_query(lookup, "intitle", "inauthor", separator=" "))
    for query in dict.fromkeys(queries):
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


def fetch_google_books(lookup: BookLookup) -> ExternalRatingResult:
    items, direct_match = _google_items(lookup)

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
    queries.append((_title_author_query(lookup, "title", "author"), None))

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
    lookup: BookLookup, hardcover_tokens: Iterable[str] = (),
) -> list[ProviderOutcome]:
    providers: dict[str, Callable[[], ExternalRatingResult]] = {
        "hardcover": lambda: fetch_hardcover(lookup, hardcover_tokens),
        "google_books": lambda: fetch_google_books(lookup),
        "open_library": lambda: fetch_open_library(lookup),
    }
    outcomes: dict[str, ProviderOutcome] = {}
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
