#!/usr/bin/env python3
"""Remove CWNG app.db rows that reference books no longer present in Calibre."""
from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path

BOOK_TABLES = (
    "annotation", "archived_book", "book_cover_lock", "book_cover_preview",
    "book_original_filename", "book_read_link", "book_shelf_link", "bookmark",
    "downloads", "favorite_book", "hardcover_book_blacklist",
    "hardcover_match_queue", "kobo_annotation_backup", "kobo_reading_state",
    "kobo_synced_books", "user_hidden_book",
)

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--app-db", required=True)
    parser.add_argument("--library-db", required=True)
    parser.add_argument("--marker")
    args=parser.parse_args()
    app=Path(args.app_db); library=Path(args.library_db)
    with sqlite3.connect(library) as source:
        valid={int(row[0]) for row in source.execute("SELECT id FROM books")}
    deleted={}
    with sqlite3.connect(app) as db:
        db.execute("PRAGMA foreign_keys=ON")
        existing={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in BOOK_TABLES:
            if table not in existing:
                continue
            stale=[int(row[0]) for row in db.execute(f'SELECT DISTINCT book_id FROM "{table}"') if int(row[0]) not in valid]
            if not stale:
                continue
            placeholders=','.join('?' for _ in stale)
            if table == "annotation" and "annotation_sync_target" in existing:
                db.execute(f'DELETE FROM annotation_sync_target WHERE annotation_id IN (SELECT id FROM annotation WHERE book_id IN ({placeholders}))', stale)
            if table == "kobo_annotation_backup":
                writable_root = app.parent.parent.resolve()
                rows = db.execute(
                    f'SELECT file_path FROM kobo_annotation_backup WHERE book_id IN ({placeholders})',
                    stale,
                ).fetchall()
                for (raw_path,) in rows:
                    if not raw_path:
                        continue
                    backup_path = Path(raw_path).expanduser().resolve(strict=False)
                    if backup_path.is_relative_to(writable_root):
                        backup_path.unlink(missing_ok=True)
            if table == "kobo_reading_state":
                ids=[row[0] for row in db.execute(f'SELECT id FROM kobo_reading_state WHERE book_id IN ({placeholders})', stale)]
                if ids:
                    p=','.join('?' for _ in ids)
                    for child in ("kobo_bookmark", "kobo_statistics"):
                        if child in existing:
                            db.execute(f'DELETE FROM "{child}" WHERE kobo_reading_state_id IN ({p})', ids)
            cur=db.execute(f'DELETE FROM "{table}" WHERE book_id IN ({placeholders})', stale)
            deleted[table]=cur.rowcount
        integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"app.db integrity check failed: {integrity}")
        db.commit()
    if args.marker:
        Path(args.marker).unlink(missing_ok=True)
    print({"valid_books": len(valid), "deleted": deleted})
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
