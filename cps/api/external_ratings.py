# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached external rating/popularity aggregates for book detail pages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from flask import jsonify

from . import api_v1
from .. import calibre_db, config, ub, logger
from ..cw_login import current_user
from ..services.external_ratings import (
    BookLookup,
    SOURCE_ORDER,
    fetch_external_ratings,
    normalize_isbn,
)
from ..usermanagement import login_required_if_no_ano

log = logger.create()

_CACHE_TTL = {
    "ok": timedelta(days=7),
    "not_found": timedelta(days=1),
    "unavailable": timedelta(hours=12),
    "error": timedelta(hours=1),
}


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _identifier_map(book):
    result = {}
    for identifier in getattr(book, "identifiers", None) or []:
        key = str(getattr(identifier, "type", "") or "").casefold().strip()
        value = str(getattr(identifier, "val", "") or "").strip()
        if key and value and key not in result:
            result[key] = value
    return result


def _identifier_value(identifiers, *keys):
    for key in keys:
        value = str(identifiers.get(key) or "").strip()
        if value:
            return value
    return None


def _split_original_authors(value):
    return tuple(
        part.strip()
        for part in re.split(r"\s*(?:;|\||\s&\s)\s*", str(value or ""))
        if part.strip()
    )


def _book_lookup(book):
    identifiers = _identifier_map(book)
    isbn = None
    for key in ("isbn13", "isbn", "isbn10"):
        normalized = normalize_isbn(identifiers.get(key))
        if normalized and (isbn is None or len(normalized) > len(isbn)):
            isbn = normalized

    hardcover_id = None
    try:
        if identifiers.get("hardcover-id"):
            hardcover_id = int(identifiers["hardcover-id"])
    except (TypeError, ValueError):
        hardcover_id = None

    pubdate = getattr(book, "pubdate", None)
    publication_year = getattr(pubdate, "year", None)
    if publication_year is not None and publication_year <= 101:
        publication_year = None

    return BookLookup(
        book_id=int(book.id),
        title=str(getattr(book, "title", "") or ""),
        authors=tuple(
            str(getattr(author, "name", "") or "").replace("|", ",")
            for author in (getattr(book, "authors", None) or [])
            if getattr(author, "name", None)
        ),
        original_title=_identifier_value(
            identifiers, "original-title", "original_title", "originaltitle",
        ),
        original_authors=_split_original_authors(_identifier_value(
            identifiers, "original-author", "original_author", "originalauthor",
        )),
        isbn=isbn,
        publication_year=publication_year,
        goodreads_id=_identifier_value(identifiers, "goodreads", "goodreads-id"),
        hardcover_id=hardcover_id,
        hardcover_slug=identifiers.get("hardcover-slug"),
        google_id=identifiers.get("google"),
        open_library_id=(identifiers.get("openlibrary")
                         or identifiers.get("open-library")
                         or identifiers.get("olid")),
    )


def _hardcover_tokens():
    values = []
    for raw in (
        getattr(current_user, "hardcover_token", None),
        config.resolved_hardcover_token(),
    ):
        cleaned = str(raw or "").replace("Bearer ", "", 1).strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values


def _google_books_api_key():
    return str(getattr(config, "config_google_books_api_key", None) or "").strip() or None


def _cache_rows(book_id):
    return (ub.session.query(ub.ExternalBookRatingCache)
            .filter(ub.ExternalBookRatingCache.book_id == int(book_id))
            .all())


def _cache_is_fresh(rows, lookup_hash):
    by_source = {row.source: row for row in rows}
    now = datetime.now(timezone.utc)
    for source in SOURCE_ORDER:
        row = by_source.get(source)
        if row is None or row.lookup_hash != lookup_hash:
            return False
        fetched_at = _utc(row.fetched_at)
        if fetched_at is None or fetched_at < now - _CACHE_TTL.get(row.status, timedelta(hours=1)):
            return False
    return True


def _clear_result_fields(row):
    row.source_id = None
    row.source_url = None
    row.matched_title = None
    row.matched_authors = []
    row.matched_by = None
    row.match_confidence = None
    row.rating = None
    row.ratings_count = None
    row.reviews_count = None
    row.popularity_count = None
    row.ratings_distribution = None


def _persist_outcomes(book_id, lookup_hash, outcomes):
    now = datetime.now(timezone.utc)
    existing = {row.source: row for row in _cache_rows(book_id)}
    for outcome in outcomes:
        row = existing.get(outcome.source)
        if row is None:
            row = ub.ExternalBookRatingCache(
                book_id=int(book_id), source=outcome.source,
                lookup_hash=lookup_hash,
            )
            ub.session.add(row)
            existing[outcome.source] = row

        # Keep the last known aggregate through a transient network/token error.
        # A real not-found result is authoritative and clears an obsolete match.
        if (outcome.status in ("error", "unavailable")
                and row.status == "ok" and row.lookup_hash == lookup_hash):
            row.error = outcome.error
            row.fetched_at = now
            continue

        row.lookup_hash = lookup_hash
        row.status = outcome.status
        row.error = outcome.error
        row.fetched_at = now
        if outcome.result is None:
            _clear_result_fields(row)
            continue

        result = outcome.result
        row.source_id = result.source_id
        row.source_url = result.source_url
        row.matched_title = result.matched_title
        row.matched_authors = result.matched_authors
        row.matched_by = result.matched_by
        row.match_confidence = result.match_confidence
        row.rating = result.rating
        row.ratings_count = result.ratings_count
        row.reviews_count = result.reviews_count
        row.popularity_count = result.popularity_count
        row.ratings_distribution = result.ratings_distribution
        row.error = None

    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        raise
    return list(existing.values())


def _serialize(rows, lookup_hash, cached):
    items = []
    errors = []
    warnings = []
    by_source = {row.source: row for row in rows if row.lookup_hash == lookup_hash}
    for source in SOURCE_ORDER:
        row = by_source.get(source)
        if row is None:
            continue
        if row.status == "ok":
            items.append({
                "source": row.source,
                "source_id": row.source_id,
                "source_url": row.source_url,
                "matched_title": row.matched_title,
                "matched_authors": row.matched_authors or [],
                "matched_by": row.matched_by,
                "match_confidence": row.match_confidence,
                "rating": row.rating,
                "ratings_count": row.ratings_count,
                "reviews_count": row.reviews_count,
                "popularity_count": row.popularity_count,
                "ratings_distribution": row.ratings_distribution,
                "fetched_at": _utc(row.fetched_at).isoformat() if row.fetched_at else None,
            })
            if row.error:
                warnings.append({"source": row.source, "message": row.error})
        else:
            errors.append({
                "source": row.source,
                "status": row.status,
                "message": row.error or "No external data found",
            })
    fetched = [_utc(row.fetched_at) for row in by_source.values() if row.fetched_at]
    return {
        "items": items,
        "errors": errors,
        "warnings": warnings,
        "cached": bool(cached),
        "fetched_at": max(fetched).isoformat() if fetched else None,
    }


def load_or_refresh_external_ratings(
    book_id,
    force=False,
    hardcover_tokens=(),
    google_books_api_key=None,
):
    """Load or refresh one book's shared external-rating cache.

    This is request-context free so upload and ingest workers can populate the
    same cache in the background. Credentials are supplied by the caller;
    request routes may include the current user's Hardcover token, while
    system tasks use server-wide configuration only.
    """
    book = calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True,
    )
    if not book:
        return None
    lookup = _book_lookup(book)
    rows = _cache_rows(book_id)
    if not force and _cache_is_fresh(rows, lookup.identity_hash):
        return _serialize(rows, lookup.identity_hash, cached=True)

    outcomes = fetch_external_ratings(
        lookup,
        hardcover_tokens,
        google_books_api_key,
    )
    rows = _persist_outcomes(book_id, lookup.identity_hash, outcomes)
    return _serialize(rows, lookup.identity_hash, cached=False)


def external_rating_summary_map(book_ids):
    """Return compact display ratings for many books in one app.db query.

    The badge intentionally represents one real source rather than inventing
    an average across communities. Among close candidates, the source with the
    largest ratings population wins; sources with at least ten ratings are
    preferred over one-off edition records.
    """
    parsed_ids = []
    for book_id in book_ids or []:
        try:
            value = int(book_id)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in parsed_ids:
            parsed_ids.append(value)
    if not parsed_ids:
        return {}

    rows = (ub.session.query(ub.ExternalBookRatingCache)
            .filter(ub.ExternalBookRatingCache.book_id.in_(parsed_ids))
            .filter(ub.ExternalBookRatingCache.status == "ok")
            .filter(ub.ExternalBookRatingCache.rating.isnot(None))
            .all())
    grouped = {}
    for row in rows:
        grouped.setdefault(int(row.book_id), []).append(row)

    priority = {source: index for index, source in enumerate(SOURCE_ORDER)}
    result = {}
    for book_id, candidates in grouped.items():
        established = [row for row in candidates if (row.ratings_count or 0) >= 10]
        pool = established or candidates
        best = max(
            pool,
            key=lambda row: (
                row.ratings_count or 0,
                row.reviews_count or 0,
                -priority.get(row.source, len(priority)),
            ),
        )
        result[book_id] = {
            "source": best.source,
            "rating": float(best.rating),
            "ratings_count": best.ratings_count,
            "source_count": len(candidates),
        }
    return result


def _load_or_refresh(book_id, force=False):
    return load_or_refresh_external_ratings(
        book_id,
        force=force,
        hardcover_tokens=_hardcover_tokens(),
        google_books_api_key=_google_books_api_key(),
    )


@api_v1.route("/books/<int:book_id>/external-ratings")
@login_required_if_no_ano
def external_book_ratings(book_id):
    payload = _load_or_refresh(book_id)
    if payload is None:
        return jsonify({"error": {"code": "not_found", "message": "Book not found"}}), 404
    return jsonify(payload)


@api_v1.route("/books/<int:book_id>/external-ratings/refresh", methods=["POST"])
@login_required_if_no_ano
def refresh_external_book_ratings(book_id):
    payload = _load_or_refresh(book_id, force=True)
    if payload is None:
        return jsonify({"error": {"code": "not_found", "message": "Book not found"}}), 404
    return jsonify(payload)
