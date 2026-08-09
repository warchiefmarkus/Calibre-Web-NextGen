# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_progress_summary_uses_newest_moon_or_native_database_timestamp():
    from cps.services.reading_progress import reading_progress_summary_map

    native = MagicMock()
    native.execute.return_value = [SimpleNamespace(
        book=7, pos_frac=0.22,
        epoch=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc).timestamp(),
    )]
    moon = MagicMock()
    moon.filter.return_value = moon
    moon.all.return_value = [SimpleNamespace(
        book_id=7, percentage=44.5,
        remote_modified=datetime(2026, 8, 2, 10, 0),
        synced_at=None, moon_timestamp=datetime(2025, 1, 1),
    )]
    session = MagicMock()
    session.query.return_value = moon

    with patch("cps.services.reading_progress.deployment_profile.use_calibre_native_reader_data",
               return_value=True):
        result = reading_progress_summary_map(
            session, 3, [7, 7, "bad"], user_name="admin", native_session=native)
    assert result == {7: {
        "percentage": 22.0,
        "updated_at": "2026-08-01T10:00:00+00:00",
        "source": "calibre_web",
    }}
    params = native.execute.call_args.args[1]
    assert params["reader_user"] == "cwng-admin"
    assert params["book_ids"] == (7,)


def test_progress_summary_prefers_native_database_when_it_is_newer():
    from cps.services.reading_progress import reading_progress_summary_map

    native = MagicMock()
    native.execute.return_value = [SimpleNamespace(
        book=9, pos_frac=0.61,
        epoch=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc).timestamp(),
    )]
    moon = MagicMock()
    moon.filter.return_value = moon
    moon.all.return_value = [SimpleNamespace(
        book_id=9, percentage=58.0,
        remote_modified=datetime(2026, 8, 2, 12, 0),
        synced_at=None, moon_timestamp=None,
    )]
    session = MagicMock()
    session.query.return_value = moon

    with patch("cps.services.reading_progress.deployment_profile.use_calibre_native_reader_data",
               return_value=True):
        result = reading_progress_summary_map(
            session, 3, [9], user_name="admin", native_session=native)
    assert result[9]["source"] == "calibre_web"
    assert result[9]["percentage"] == 61.0
    assert reading_progress_summary_map(MagicMock(), None, [9]) == {}


def test_progress_summary_does_not_let_newer_zero_hide_real_moon_progress():
    from cps.services.reading_progress import reading_progress_summary_map

    native = MagicMock()
    native.execute.return_value = [SimpleNamespace(
        book=156, pos_frac=0.0,
        epoch=datetime(2026, 8, 3, 13, 38, tzinfo=timezone.utc).timestamp(),
    )]
    moon = MagicMock()
    moon.filter.return_value = moon
    moon.all.return_value = [SimpleNamespace(
        book_id=156, percentage=28.5,
        remote_modified=datetime(2026, 4, 10, 23, 31),
        synced_at=None, moon_timestamp=None,
    )]
    session = MagicMock()
    session.query.return_value = moon

    with patch("cps.services.reading_progress.deployment_profile.use_calibre_native_reader_data",
               return_value=True):
        result = reading_progress_summary_map(
            session, 1, [156], user_name="admin", native_session=native)
    assert result[156]["percentage"] == 28.5
    assert result[156]["source"] == "moonreader"


def test_progress_summary_keeps_moon_data_when_native_query_fails():
    from cps.services.reading_progress import reading_progress_summary_map

    native = MagicMock()
    native.execute.side_effect = RuntimeError("native database unavailable")
    moon = MagicMock()
    moon.filter.return_value = moon
    moon.all.return_value = [SimpleNamespace(
        book_id=12, percentage=73.0,
        remote_modified=datetime(2026, 8, 2, 9, 0),
        synced_at=None, moon_timestamp=None,
    )]
    session = MagicMock()
    session.query.return_value = moon

    with patch("cps.services.reading_progress.deployment_profile.use_calibre_native_reader_data",
               return_value=True):
        result = reading_progress_summary_map(
            session, 3, [12], user_name="admin", native_session=native)
    assert result[12]["percentage"] == 73.0
    assert result[12]["source"] == "moonreader"


def test_progress_summary_keeps_legacy_app_database_fallback():
    from cps.services.reading_progress import reading_progress_summary_map

    kobo = MagicMock()
    kobo.join.return_value = kobo
    kobo.filter.return_value = kobo
    kobo.all.return_value = [SimpleNamespace(
        book_id=11, percentage=36.0,
        bookmark_modified=datetime(2026, 8, 3, 12, 0), state_modified=None,
    )]
    moon = MagicMock()
    moon.filter.return_value = moon
    moon.all.return_value = []
    session = MagicMock()
    session.query.side_effect = [kobo, moon]

    with patch("cps.services.reading_progress.deployment_profile.use_calibre_native_reader_data",
               return_value=False):
        result = reading_progress_summary_map(session, 3, [11])
    assert result[11]["percentage"] == 36.0
    assert result[11]["source"] == "calibre_web"

def test_catalog_and_detail_render_progress_badges():
    api = (ROOT / "frontend/src/lib/api.ts").read_text(encoding="utf-8")
    cover = (ROOT / "frontend/src/components/BookCover.tsx").read_text(encoding="utf-8")
    card = (ROOT / "frontend/src/components/BookCard.tsx").read_text(encoding="utf-8")
    badge = (ROOT / "frontend/src/components/CoverProgressBadge.tsx").read_text(encoding="utf-8")
    badge_css = (ROOT / "frontend/src/components/CoverProgressBadge.module.css").read_text(encoding="utf-8")
    card_css = (ROOT / "frontend/src/components/BookCard.module.css").read_text(encoding="utf-8")
    detail = (ROOT / "frontend/src/pages/BookDetail.tsx").read_text(encoding="utf-8")
    moon = (ROOT / "frontend/src/pages/MoonReaderSync.tsx").read_text(encoding="utf-8")
    moon_css = (ROOT / "frontend/src/pages/MoonReaderSync.module.css").read_text(encoding="utf-8")
    moon_service = (ROOT / "cps/services/moonreader_webdav.py").read_text(encoding="utf-8")

    assert "export interface ReadingProgressSummary" in api
    assert "reading_progress?: ReadingProgressSummary" in api
    assert "<CoverProgressBadge progress={readingProgress}" in cover
    assert "readingProgress={book.reading_progress}" in card
    assert "data-cover-progress" in badge
    assert "BookOpen" not in badge
    assert "left: var(--sp-2)" in badge_css and "top: var(--sp-2)" in badge_css
    assert "background: rgba(20, 28, 36, .70)" in badge_css
    assert '<CoverProgressBadge progress={book.reading_progress} side="right" />' in detail
    assert "unifiedProgress" in detail
    assert "externalRatingName" not in detail
    assert "KOReader Progress" not in detail
    assert 'coverReadHint' not in detail
    reader = (ROOT / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    progress_lib = (ROOT / "frontend/src/lib/readerProgress.ts").read_text(encoding="utf-8")
    assert "formatReadingProgress(canonicalProgress * 100)" in reader
    assert "savedPositionFraction ?? positionQuery.data?.position_fraction" in reader
    assert "Math.round(progress * 100)" not in reader
    assert "Math.round(value * 10) / 10" in progress_lib
    assert '<BookOpen size={20} />' not in detail
    assert "top: 35px" not in card_css and "top: 68px" not in card_css
    assert "max-width: 132px" in badge_css
    assert "className={styles.fileList}" in moon
    assert ".fileList li" in moon_css
    assert "MAX_SUMMARY_ITEMS = 500" in moon_service
