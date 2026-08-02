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


def test_external_ratings_have_independent_query_and_manual_refresh():
    assert "useExternalBookRatings" in QUERIES
    assert "useRefreshExternalBookRatings" in QUERIES
    assert "/external-ratings/refresh" in QUERIES
    assert "queryClient.setQueryData(['external-book-ratings'" in QUERIES


def test_book_detail_keeps_sources_separate_and_shows_aggregate_counts():
    assert "<ExternalRatingsPanel bookId={book.id} />" in DETAIL
    assert "EXTERNAL_RATING_LABELS" in DETAIL
    assert "Ratings: {count}" in DETAIL
    assert "Reviews: {count}" in DETAIL
    assert "Readers: {count}" in DETAIL
    assert "ExternalBookRatingsResponse" in API
    assert ".externalRatingsGrid" in CSS
