#!/root/calibre/CalibreWeb/source/.venv/bin/python
"""Copy legacy CWNG bookmarks/annotations into Calibre-native reader storage.

Dry-run is the default. Use --apply explicitly. Existing native positions and
annotation UUIDs are never overwritten, and legacy app.db rows are never deleted.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import requests

DEFAULT_APP_DB = Path("/root/calibre/CalibreWeb/var/config/app.db")
DEFAULT_ENV = Path("/root/calibre/CalibreWeb/var/config/cwng.env")
COLORS = {"yellow", "red", "green", "blue"}


def load_env(path: Path) -> None:
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def api_request(
    method: str,
    path: str,
    user: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = os.environ.get("CWNG_MCP_URL", "http://127.0.0.1:10720").rstrip("/")
    token = os.environ.get("CWNG_CALIBREMCP_REST_TOKEN", "").strip()
    if not token:
        raise RuntimeError("CWNG_CALIBREMCP_REST_TOKEN is not configured")
    response = requests.request(
        method,
        base + path,
        headers={
            "Authorization": f"Bearer {token}",
            "X-CWNG-User": user,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        params=params,
        json=payload,
        timeout=(3, 30),
    )
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = data.get("error", {}).get("message") if isinstance(data, dict) else None
        raise RuntimeError(message or f"CalibreMCP returned HTTP {response.status_code}")
    return data if isinstance(data, dict) else {}


def split_cfi_range(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    bracket_depth = 0
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "^":
            current.append(char)
            escaped = True
            continue
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        if char == "," and bracket_depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def cfi_to_native(cfi_range: str) -> dict[str, Any]:
    value = (cfi_range or "").strip()
    if not value.startswith("epubcfi(") or not value.endswith(")") or "!" not in value:
        raise ValueError("unsupported or missing EPUB CFI")
    package_path, content = value[len("epubcfi("):-1].split("!", 1)
    parts = split_cfi_range(content)
    if len(parts) == 1:
        start = end = "!" + parts[0]
    elif len(parts) == 2:
        start, end = ("!" + part for part in parts)
    elif len(parts) == 3:
        common, start_tail, end_tail = parts
        start, end = "!" + common + start_tail, "!" + common + end_tail
    else:
        raise ValueError("unsupported EPUB CFI range shape")
    steps = [part for part in package_path.split("/") if part]
    try:
        spine_index = max(0, int(steps[1].split("[", 1)[0]) // 2 - 1)
    except (IndexError, ValueError):
        spine_index = 0
    return {"start_cfi": start, "end_cfi": end, "spine_index": spine_index}


def legacy_rows(db: sqlite3.Connection, user: str):
    db.row_factory = sqlite3.Row
    bookmarks = db.execute(
        """SELECT b.*, u.name AS user_name
           FROM bookmark b JOIN user u ON u.id=b.user_id
           WHERE lower(u.name)=lower(?) ORDER BY b.id""",
        (user,),
    ).fetchall()
    annotations = db.execute(
        """SELECT a.*, u.name AS user_name
           FROM annotation a JOIN user u ON u.id=a.user_id
           WHERE lower(u.name)=lower(?) AND coalesce(a.hidden,0)=0
           ORDER BY a.id""",
        (user,),
    ).fetchall()
    return bookmarks, annotations


def migrate_bookmarks(rows, user: str, apply: bool, summary: dict[str, int]) -> None:
    for row in rows:
        fmt = str(row["format"] or "EPUB").upper()
        existing = api_request(
            "GET",
            f"/api/v1/reader/books/{row['book_id']}/position",
            user,
            params={"format": fmt},
        ).get("positions", [])
        if existing:
            summary["bookmarks_existing"] += 1
            continue
        if not row["bookmark_key"]:
            summary["bookmarks_skipped"] += 1
            continue
        summary["bookmarks_candidates"] += 1
        if apply:
            api_request(
                "PUT",
                f"/api/v1/reader/books/{row['book_id']}/position",
                user,
                payload={
                    "format": fmt,
                    "device": "cwng-legacy-migration",
                    "cfi": row["bookmark_key"],
                    "position_fraction": 0.0,
                },
            )
            summary["bookmarks_migrated"] += 1


def migrate_annotations(rows, user: str, apply: bool, summary: dict[str, int]) -> None:
    existing_by_book: dict[int, set[str]] = {}
    for row in rows:
        book_id = int(row["book_id"])
        if book_id not in existing_by_book:
            payload = api_request(
                "GET",
                f"/api/v1/reader/books/{book_id}/annotations",
                user,
                params={"format": "EPUB"},
            )
            annotation_map = payload.get("annotations_map", {})
            existing_by_book[book_id] = {
                str(item.get("uuid"))
                for item in annotation_map.get("highlight", [])
                if isinstance(item, dict) and item.get("uuid")
            }
        annotation_id = str(row["annotation_id"] or "")
        if not annotation_id or annotation_id in existing_by_book[book_id]:
            summary["annotations_existing"] += 1
            continue
        try:
            native = cfi_to_native(str(row["cfi_range"] or ""))
        except ValueError:
            summary["annotations_skipped"] += 1
            continue
        content_id = str(row["content_id"] or "")
        spine_name = content_id.split("!!", 1)[1] if "!!" in content_id else ""
        color = str(row["highlight_color"] or "yellow").lower()
        if color not in COLORS:
            color = "yellow"
        summary["annotations_candidates"] += 1
        if apply:
            api_request(
                "POST",
                f"/api/v1/reader/books/{book_id}/annotations",
                user,
                payload={
                    "format": "EPUB",
                    "type": "highlight",
                    "uuid": annotation_id,
                    "start_cfi": native["start_cfi"],
                    "end_cfi": native["end_cfi"],
                    "spine_index": native["spine_index"],
                    "spine_name": spine_name,
                    "cfi_range": row["cfi_range"],
                    "highlighted_text": row["highlighted_text"],
                    "notes": row["note_text"],
                    "style": {"kind": "color", "which": color},
                    "chapter_progress": row["chapter_progress"],
                    "context_string": row["context_string"],
                    "content_id": row["content_id"],
                    "source": "legacy-cwng-migration",
                },
            )
            existing_by_book[book_id].add(annotation_id)
            summary["annotations_migrated"] += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform writes")
    parser.add_argument("--user", default="admin", help="CWNG user mapped in CalibreMCP")
    parser.add_argument("--app-db", type=Path, default=DEFAULT_APP_DB)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    args = parser.parse_args()
    load_env(args.env_file)
    summary = {
        "bookmarks_candidates": 0,
        "bookmarks_existing": 0,
        "bookmarks_skipped": 0,
        "bookmarks_migrated": 0,
        "annotations_candidates": 0,
        "annotations_existing": 0,
        "annotations_skipped": 0,
        "annotations_migrated": 0,
    }
    with sqlite3.connect(args.app_db) as db:
        bookmarks, annotations = legacy_rows(db, args.user)
    migrate_bookmarks(bookmarks, args.user, args.apply, summary)
    migrate_annotations(annotations, args.user, args.apply, summary)
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "user": args.user,
        "legacy_bookmarks": len(bookmarks),
        "legacy_annotations": len(annotations),
        **summary,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
