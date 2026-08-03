# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
DETAIL = (ROOT / "frontend/src/pages/BookDetail.tsx").read_text(encoding="utf-8")
QUERIES = (ROOT / "frontend/src/lib/queries.ts").read_text(encoding="utf-8")
API = (ROOT / "frontend/src/lib/api.ts").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/src/pages/BookDetail.module.css").read_text(encoding="utf-8")
BOOK_COVER = (ROOT / "frontend/src/components/BookCover.tsx").read_text(encoding="utf-8")
BOOK_CARD = (ROOT / "frontend/src/components/BookCard.tsx").read_text(encoding="utf-8")
BADGE = (ROOT / "frontend/src/components/CoverRatingBadge.tsx").read_text(encoding="utf-8")
BADGE_CSS = (ROOT / "frontend/src/components/CoverRatingBadge.module.css").read_text(encoding="utf-8")
CARD_CSS = (ROOT / "frontend/src/components/BookCard.module.css").read_text(encoding="utf-8")


def test_external_ratings_refresh_invalidates_every_preview_surface():
    assert "useExternalBookRatings" in QUERIES
    assert "useRefreshExternalBookRatings" in QUERIES
    assert "/external-ratings/refresh" in QUERIES
    assert "queryClient.setQueryData(['external-book-ratings'" in QUERIES
    assert "refetchOnMount: 'always'" in QUERIES
    assert "/external-ratings/refresh-async" in QUERIES
    assert "useEffect(() =>" in QUERIES
    assert "retried at most once per day" in QUERIES
    assert "query.state.data?.refreshing ? 1_500 : false" in QUERIES
    assert "ratings.data?.refreshing === true" in DETAIL
    assert "backgroundRefreshing ? t('Loading ratings…')" in DETAIL
    assert "aria-busy={ratingBusy}" in DETAIL
    assert "disabled={ratingBusy}" in DETAIL
    assert "refreshing?: boolean" in API
    for key in ("books", "adv-search", "discover-strip", "shelf", "magicshelf"):
        assert f"queryKey: ['{key}']" in QUERIES


def test_book_detail_uses_compact_source_badges_without_provider_noise():
    assert "<ExternalRatingsPanel bookId={book.id} readingProgress={book.reading_progress} />" in DETAIL
    assert "EXTERNAL_RATING_SOURCE_LABELS" in DETAIL
    assert "formatExternalRatingScore" in DETAIL
    assert "Ratings: {count}" in DETAIL
    assert "Reviews: {count}" in DETAIL
    assert "Readers: {count}" not in DETAIL
    assert "sources unavailable" not in DETAIL
    assert "Updated {date}" not in DETAIL
    assert "ExternalBookRatingsResponse" in API
    assert ".externalRatingBadge" in CSS
    assert ".externalRatingsGrid" not in CSS


def test_cover_rating_badge_is_shared_and_placed_top_right():
    assert "externalRating?: ExternalRatingSummary" in BOOK_COVER
    assert "<CoverRatingBadge rating={externalRating}" in BOOK_COVER
    assert "externalRating={book.external_rating}" in BOOK_CARD
    assert "formatExternalRatingScore" in BADGE
    assert "data-cover-rating" in BADGE
    assert "[data-cover-rating]" in CARD_CSS
    assert ".wrap:hover .coverWrap [data-cover-rating]" in CARD_CSS
    assert ".cardSelected .coverWrap [data-cover-rating]" in CARD_CSS
    assert "opacity: 0" in CARD_CSS
    assert "opacity: 1" in CARD_CSS
    assert "top: var(--sp-2)" in BADGE_CSS
    assert "bottom: var(--sp-2)" not in BADGE_CSS
    assert "@container book-card" in BADGE_CSS
