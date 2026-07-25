#!/root/calibre/CalibreWeb/source/.venv/bin/python
"""Validate authenticated managed-profile contracts without persistent writes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import time
import uuid

from flask import Flask
from flask.sessions import SecureCookieSessionInterface
import requests

BASE = "http://127.0.0.1:18083"
APP_DB = Path("/root/calibre/CalibreWeb/var/config/app.db")
LIBRARY_DB = Path("/root/calibre/Library/metadata.db")
ENV_FILE = Path("/root/calibre/CalibreWeb/var/config/cwng.env")
USER_AGENT = "cwng-managed-contract-validation/1.0"


def load_env() -> None:
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def wait_for_service(timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(BASE + "/api/v1/auth/csrf", timeout=3)
            if response.status_code < 500:
                return
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"CWNG did not become ready within {timeout_seconds}s: {last_error}")


def make_session() -> tuple[requests.Session, int]:
    wait_for_service()
    app = Flask("cwng-contract-signer")
    app.secret_key = os.environ["SECRET_KEY"]
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    if serializer is None:
        raise RuntimeError("Could not create CWNG session signer")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    csrf_response = session.get(BASE + "/api/v1/auth/csrf", timeout=20)
    csrf_response.raise_for_status()
    csrf_token = csrf_response.json()["csrf_token"]
    cookie_data = serializer.loads(session.cookies.get("session"))

    random_value = uuid.uuid4().hex
    identifier = hashlib.sha512(
        f"{b'127.0.0.1'}|{USER_AGENT.encode()}".encode()
    ).hexdigest()
    cookie_data.update({
        "_user_id": "1",
        "_fresh": True,
        "_id": identifier,
        "_random": random_value,
    })
    with sqlite3.connect(APP_DB) as db:
        row_id = db.execute(
            "INSERT INTO user_session(user_id,session_key,random,expiry) "
            "VALUES(?,?,?,?)",
            (1, identifier, random_value, int(time.time()) + 3600),
        ).lastrowid
        db.commit()

    session.cookies.clear()
    session.cookies.set(
        "session", serializer.dumps(cookie_data), domain="127.0.0.1", path="/"
    )
    session.headers.update({"X-CSRFToken": csrf_token})
    return session, row_id


def assert_disabled(response: requests.Response) -> None:
    if response.status_code != 404:
        raise AssertionError(
            f"Expected HTTP 404, got {response.status_code}: {response.text[:300]}"
        )
    if response.json().get("error", {}).get("code") != "feature_disabled":
        raise AssertionError(f"Wrong disabled feature contract: {response.text[:300]}")


def main() -> int:
    load_env()
    session, session_row_id = make_session()
    test_shelf_name = "cwng-managed-validation-" + uuid.uuid4().hex
    try:
        with sqlite3.connect(APP_DB) as db:
            shelves_before = db.execute("SELECT COUNT(*) FROM shelf").fetchone()[0]

        me = session.get(BASE + "/api/v1/auth/me", timeout=20)
        me.raise_for_status()
        features = me.json()["features"]
        assert features.get("kobo_sync") is False
        assert features.get("kobo_sync_magic_shelves") is False
        assert features.get("rag_search") is True

        rag_status = session.get(BASE + "/api/v1/rag/status", timeout=30)
        rag_status.raise_for_status()
        rag_state = rag_status.json()
        assert rag_state.get("ready") is True
        assert int(rag_state.get("indexed_books") or 0) > 0
        assert not {
            "library_path", "database_path", "vector_store", "model_revision"
        }.intersection(rag_state)

        rag_search = session.post(
            BASE + "/api/v1/rag/search",
            json={"query": "імператор", "mode": "hybrid", "limit": 1},
            timeout=120,
        )
        rag_search.raise_for_status()
        rag_result = rag_search.json()
        assert 0 <= int(rag_result.get("count") or 0) <= 1
        assert all(len(item.get("text") or "") <= 2800
                   for item in rag_result.get("results", []))

        # Metadata is the first managed library mutation intentionally opened.
        # Exercise its field-validation branch with an invalid title: the route
        # must be reachable, preserve the legacy per-field error envelope, and
        # must not call CalibreMCP or mutate book 1.
        metadata_before = session.get(
            BASE + "/api/v1/books/1/metadata", timeout=30
        )
        metadata_before.raise_for_status()
        invalid_metadata = session.post(
            BASE + "/api/v1/books/1/metadata",
            json={"title": ""}, timeout=30,
        )
        invalid_metadata.raise_for_status()
        invalid_body = invalid_metadata.json()
        assert "title" in invalid_body.get("errors", {})
        metadata_after = session.get(
            BASE + "/api/v1/books/1/metadata", timeout=30
        )
        metadata_after.raise_for_status()
        assert metadata_after.json()["title"] == metadata_before.json()["title"]

        # Cover route is open through the managed adapter, but malformed bytes
        # must fail before staging/CalibreMCP and leave the real book untouched.
        with sqlite3.connect(LIBRARY_DB) as db:
            cover_before = db.execute(
                "SELECT has_cover FROM books WHERE id=1"
            ).fetchone()[0]
        invalid_cover = session.post(
            BASE + "/api/v1/books/1/cover",
            files={"file": ("not-cover.jpg", b"not-an-image", "image/jpeg")},
            timeout=30,
        )
        assert invalid_cover.status_code == 400, invalid_cover.text
        assert invalid_cover.json().get("error", {}).get("code") == "invalid_cover"
        with sqlite3.connect(LIBRARY_DB) as db:
            cover_after = db.execute(
                "SELECT has_cover FROM books WHERE id=1"
            ).fetchone()[0]
        assert cover_after == cover_before

        # Existing-book format upload is open, but the configured extension
        # whitelist must reject executable input before staging or CalibreMCP.
        with sqlite3.connect(LIBRARY_DB) as db:
            formats_before = db.execute(
                "SELECT COUNT(*) FROM data WHERE book=1"
            ).fetchone()[0]
        invalid_format = session.post(
            BASE + "/api/v1/books/1/formats",
            files={"file": ("not-a-book.exe", b"MZ", "application/octet-stream")},
            timeout=30,
        )
        assert invalid_format.status_code == 400, invalid_format.text
        with sqlite3.connect(LIBRARY_DB) as db:
            formats_after = db.execute(
                "SELECT COUNT(*) FROM data WHERE book=1"
            ).fetchone()[0]
        assert formats_after == formats_before

        # Whole-book import/delete and conversion are open managed adapters too.
        # Exercise only non-destructive rejection paths in the routine validator.
        with sqlite3.connect(LIBRARY_DB) as db:
            books_before = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        invalid_upload = session.post(
            BASE + "/api/v1/upload",
            files={"file": ("not-a-book.exe", b"MZ", "application/octet-stream")},
            timeout=30,
        )
        invalid_upload.raise_for_status()
        invalid_upload_body = invalid_upload.json()
        assert invalid_upload_body.get("queued") == []
        assert invalid_upload_body.get("errors")

        invalid_conversion = session.post(
            BASE + "/api/v1/books/1/convert",
            json={"from": "PDF", "to": "EXE"}, timeout=30,
        )
        assert invalid_conversion.status_code == 400, invalid_conversion.text

        missing_delete = session.post(
            BASE + "/api/v1/books/999999/delete", timeout=30,
        )
        assert missing_delete.status_code == 404, missing_delete.text
        with sqlite3.connect(LIBRARY_DB) as db:
            books_after = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        assert books_after == books_before

        account = session.get(BASE + "/api/v1/account", timeout=20)
        account.raise_for_status()
        assert account.json().get("kobo_only_shelves_sync") is False

        assert_disabled(session.post(
            BASE + "/api/v1/account/profile",
            json={"kobo_only_shelves_sync": True}, timeout=20,
        ))
        assert_disabled(session.post(
            BASE + "/api/v1/shelves",
            json={"name": test_shelf_name, "kobo_sync": True}, timeout=20,
        ))

        magic = session.get(BASE + "/api/v1/magicshelves", timeout=20)
        magic.raise_for_status()
        magic_items = magic.json().get("items", [])
        assert all(item.get("kobo_sync") is False for item in magic_items)
        if magic_items:
            assert_disabled(session.post(
                BASE + f"/api/v1/magicshelf/{magic_items[0]['id']}/kobo-sync",
                json={"kobo_sync": True}, timeout=20,
            ))

        bookmark = session.get(
            BASE + "/api/v1/books/1/bookmark?format=epub", timeout=30
        )
        bookmark.raise_for_status()
        annotations = session.get(BASE + "/annotations/1/data.json", timeout=30)
        annotations.raise_for_status()

        with sqlite3.connect(APP_DB) as db:
            shelves_after = db.execute("SELECT COUNT(*) FROM shelf").fetchone()[0]
            accidental = db.execute(
                "SELECT COUNT(*) FROM shelf WHERE name=?", (test_shelf_name,)
            ).fetchone()[0]
        assert shelves_before == shelves_after
        assert accidental == 0
        print("CWNG authenticated managed contract passed")
        return 0
    finally:
        with sqlite3.connect(APP_DB) as db:
            db.execute("DELETE FROM user_session WHERE id=?", (session_row_id,))
            db.commit()


if __name__ == "__main__":
    raise SystemExit(main())
