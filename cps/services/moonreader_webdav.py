# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Moon+ Reader WebDAV position import.

Moon+ stores one small ``.po`` locator per book. The raw locator is retained
for future exact-position adapters; the normalized percentage is mirrored into
CWNG's existing progress model so the library and book detail UI can use it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import os
import re
import unicodedata
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import selectinload

from .. import calibre_db, cli_param, config, config_sql, db, logger, ub

log = logger.create()

DEFAULT_BASE_URL = "http://192.168.31.150:18283/books/"
DEFAULT_USERNAME = "reader"
DEFAULT_CACHE_PATHS = (".Moon+/Cache", "Books/.Moon+/Cache", "books/.Moon+/Cache")
MAX_PROPFIND_BYTES = 5 * 1024 * 1024
MAX_POSITION_BYTES = 64 * 1024
MAX_CHECKSUM_BOOK_BYTES = 100 * 1024 * 1024
MAX_SUMMARY_ITEMS = 500
_DAV = "{DAV:}"
_PO_RE = re.compile(
    r"^\s*(?P<timestamp>\d{10,16})\*(?P<chapter>-?\d+)"
    r"(?:@(?P<locator>.+))?:(?P<percentage>\d+(?:[.,]\d+)?)%\s*$"
)


class MoonReaderError(Exception):
    def __init__(self, message: str, *, code: str = "moonreader_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class MoonPosition:
    raw: str
    timestamp: datetime
    chapter: int
    locator: str
    percentage: float


@dataclass(frozen=True)
class WebDavResource:
    path: str
    is_collection: bool
    size: int | None = None
    etag: str | None = None
    modified: datetime | None = None


@dataclass(frozen=True)
class BookMatch:
    book_id: int
    format: str | None
    filename: str
    method: str


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_base_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        raise MoonReaderError("WebDAV URL is required and must be at most 2048 characters.", code="invalid_url")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MoonReaderError("WebDAV URL must use http or https.", code="invalid_url")
    if parsed.username is not None or parsed.password is not None:
        raise MoonReaderError("Put WebDAV credentials in their own fields, not in the URL.", code="invalid_url")
    if parsed.query or parsed.fragment:
        raise MoonReaderError("WebDAV URL cannot contain a query or fragment.", code="invalid_url")
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_cache_path(value: Any) -> str:
    raw = str(value or "").strip().strip("/")
    if not raw:
        return ""
    if len(raw) > 1024 or "\x00" in raw:
        raise MoonReaderError("Moon+ cache path is invalid.", code="invalid_cache_path")
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise MoonReaderError("Moon+ cache path cannot contain traversal segments.", code="invalid_cache_path")
    return "/".join(parts)


def _fernet() -> Fernet:
    key, error = config_sql.get_encryption_key(os.path.dirname(cli_param.settings_path))
    if error:
        raise MoonReaderError(
            "Could not access the server encryption key.",
            code="encryption_unavailable", status=500,
        )
    return Fernet(key)


def encrypt_password(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) > 4096:
        raise MoonReaderError("WebDAV password is too long.", code="invalid_password")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_password(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        log.error("Could not decrypt Moon+ Reader WebDAV password: %s", exc)
        raise MoonReaderError(
            "Stored WebDAV password cannot be decrypted.",
            code="invalid_stored_password", status=500,
        ) from exc


def parse_position(value: bytes | str) -> MoonPosition:
    if isinstance(value, bytes):
        if len(value) > MAX_POSITION_BYTES:
            raise MoonReaderError("Moon+ position file is too large.", code="invalid_position")
        try:
            text = value.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MoonReaderError("Moon+ position file is not UTF-8.", code="invalid_position") from exc
    else:
        text = str(value)
    match = _PO_RE.fullmatch(text)
    if not match:
        raise MoonReaderError("Moon+ position file has an unsupported format.", code="invalid_position")
    percentage = float(match.group("percentage").replace(",", "."))
    if percentage < 0 or percentage > 100:
        raise MoonReaderError("Moon+ percentage is outside 0-100.", code="invalid_position")
    raw_timestamp = int(match.group("timestamp"))
    seconds = raw_timestamp / 1000.0 if raw_timestamp >= 100_000_000_000 else float(raw_timestamp)
    try:
        timestamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise MoonReaderError("Moon+ timestamp is invalid.", code="invalid_position") from exc
    return MoonPosition(
        raw=text.strip(),
        timestamp=timestamp,
        chapter=int(match.group("chapter")),
        # Reflowable formats include an exact ``@locator``. PDF positions only
        # contain the page number before ``:``, so retain that as the locator.
        locator=match.group("locator") or match.group("chapter"),
        percentage=percentage,
    )


class WebDavClient:
    def __init__(self, base_url: str, username: str, password: str, *, timeout: float = 15.0):
        self.base_url = normalize_base_url(base_url)
        self.username = str(username or "").strip()
        if not self.username:
            raise MoonReaderError("WebDAV username is required.", code="invalid_username")
        if not password:
            raise MoonReaderError("WebDAV password is not configured.", code="password_required")
        self.timeout = max(3.0, min(float(timeout), 60.0))
        self.session = requests.Session()
        self.session.auth = (self.username, password)
        self.session.headers.update({"User-Agent": "Calibre-Web-NextGen/MoonReaderSync"})
        parsed = urlsplit(self.base_url)
        self._origin = (parsed.scheme.lower(), parsed.netloc.lower())
        self._base_path = unquote(parsed.path)

    def close(self):
        self.session.close()

    def _url(self, relative_path: str = "", *, collection: bool = False) -> str:
        path = normalize_cache_path(relative_path)
        if not path:
            return self.base_url
        encoded = "/".join(quote(part, safe="") for part in path.split("/"))
        if collection:
            encoded += "/"
        return urljoin(self.base_url, encoded)

    def _request(self, method: str, relative_path: str = "", *, collection: bool = False, **kwargs):
        try:
            response = self.session.request(
                method, self._url(relative_path, collection=collection), timeout=self.timeout,
                allow_redirects=False, **kwargs,
            )
        except requests.RequestException as exc:
            raise MoonReaderError(
                "Could not connect to the WebDAV server.",
                code="connection_failed", status=502,
            ) from exc
        if 300 <= response.status_code < 400:
            response.close()
            raise MoonReaderError("WebDAV redirects are not accepted.", code="unexpected_redirect", status=502)
        if response.status_code in {401, 403}:
            response.close()
            raise MoonReaderError("WebDAV credentials were rejected.", code="authentication_failed", status=401)
        return response

    def _relative_href(self, href: str) -> str | None:
        parsed = urlsplit(href)
        if parsed.netloc and (parsed.scheme.lower(), parsed.netloc.lower()) != self._origin:
            return None
        path = unquote(parsed.path)
        base = self._base_path
        if path.rstrip("/") == base.rstrip("/"):
            return ""
        if not path.startswith(base):
            return None
        relative = path[len(base):].strip("/")
        try:
            return normalize_cache_path(relative)
        except MoonReaderError:
            return None

    def propfind(self, relative_path: str = "", *, depth: int = 1) -> list[WebDavResource] | None:
        body = b'<?xml version="1.0"?><propfind xmlns="DAV:"><allprop/></propfind>'
        response = self._request(
            "PROPFIND", relative_path, collection=True,
            headers={"Depth": str(depth), "Content-Type": "application/xml"},
            data=body,
        )
        try:
            if response.status_code == 404:
                return None
            if response.status_code != 207:
                raise MoonReaderError(
                    f"WebDAV PROPFIND returned HTTP {response.status_code}.",
                    code="webdav_error", status=502,
                )
            content = response.content
            if len(content) > MAX_PROPFIND_BYTES:
                raise MoonReaderError("WebDAV directory listing is too large.", code="response_too_large", status=502)
        finally:
            response.close()
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise MoonReaderError("WebDAV returned invalid XML.", code="invalid_webdav_xml", status=502) from exc
        resources: list[WebDavResource] = []
        for node in root.findall(f"{_DAV}response"):
            href = node.findtext(f"{_DAV}href") or ""
            relative = self._relative_href(href)
            if relative is None:
                continue
            prop = node.find(f"{_DAV}propstat/{_DAV}prop")
            if prop is None:
                continue
            resource_type = prop.find(f"{_DAV}resourcetype")
            is_collection = bool(resource_type is not None and resource_type.find(f"{_DAV}collection") is not None)
            size = None
            try:
                size_text = prop.findtext(f"{_DAV}getcontentlength")
                size = int(size_text) if size_text else None
            except (TypeError, ValueError):
                size = None
            modified = None
            modified_text = prop.findtext(f"{_DAV}getlastmodified")
            if modified_text:
                try:
                    modified = _aware(parsedate_to_datetime(modified_text))
                except (TypeError, ValueError, OverflowError):
                    modified = None
            resources.append(WebDavResource(
                path=relative,
                is_collection=is_collection,
                size=size,
                etag=prop.findtext(f"{_DAV}getetag"),
                modified=modified,
            ))
        return resources

    def get_bytes(self, relative_path: str, *, limit: int = MAX_POSITION_BYTES) -> bytes:
        response = self._request("GET", relative_path, stream=True)
        try:
            if response.status_code != 200:
                raise MoonReaderError(
                    f"WebDAV GET returned HTTP {response.status_code}.",
                    code="webdav_error", status=502,
                )
            chunks = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise MoonReaderError("WebDAV file exceeds the allowed size.", code="response_too_large", status=502)
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()

    def sha256(self, relative_path: str, *, limit: int = MAX_CHECKSUM_BOOK_BYTES) -> tuple[str, int]:
        response = self._request("GET", relative_path, stream=True)
        try:
            if response.status_code != 200:
                raise MoonReaderError(
                    f"WebDAV book GET returned HTTP {response.status_code}.",
                    code="webdav_error", status=502,
                )
            digest = hashlib.sha256()
            size = 0
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise MoonReaderError("Remote book is too large for checksum matching.", code="book_too_large", status=422)
                digest.update(chunk)
            return digest.hexdigest(), size
        finally:
            response.close()

    def discover_positions(self, cache_path: str = "") -> tuple[str, list[WebDavResource], bool]:
        candidates = (normalize_cache_path(cache_path),) if cache_path else DEFAULT_CACHE_PATHS
        for candidate in candidates:
            resources = self.propfind(candidate, depth=1)
            if resources is None:
                continue
            files = [row for row in resources if not row.is_collection and row.path.casefold().endswith(".po")]
            return candidate, files, True
        return candidates[0], [], False

    def root_files(self) -> list[WebDavResource]:
        rows = self.propfind("", depth=1) or []
        return [row for row in rows if row.path and not row.is_collection]


class BookMatcher:
    def __init__(self):
        self._exact: dict[str, list[BookMatch]] = {}
        self._stem: dict[str, list[BookMatch]] = {}
        self._by_id_format: dict[tuple[int, str], BookMatch] = {}
        self._local_files: list[tuple[BookMatch, str, int | None]] = []
        originals = {
            int(row.book_id): row.filename
            for row in ub.session.query(ub.BookOriginalFilename).all()
        }
        books = (calibre_db.session.query(db.Books)
                 .options(selectinload(db.Books.data))
                 .all())
        for book in books:
            for item in book.data or []:
                fmt = str(item.format or "").lower()
                name = str(item.name or "")
                filename = name if name.casefold().endswith(f".{fmt}") else f"{name}.{fmt}"
                match = BookMatch(int(book.id), fmt.upper(), filename, "filename")
                self._add(filename, match)
                self._by_id_format[(int(book.id), fmt.upper())] = match
                path = os.path.join(config.config_calibre_dir, book.path, filename)
                self._local_files.append((match, path, getattr(item, "uncompressed_size", None)))
            original = originals.get(int(book.id))
            if original:
                self._add(original, BookMatch(int(book.id), _extension(original), original, "original_filename"))

    @staticmethod
    def _key(value: str) -> str:
        value = unicodedata.normalize("NFKC", unquote(os.path.basename(value))).casefold()
        return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))

    @staticmethod
    def _stem_key(value: str) -> str:
        base = unquote(os.path.basename(value))
        if base.casefold().endswith(".po"):
            base = base[:-3]
        stem, _ = os.path.splitext(base)
        return BookMatcher._key(stem)

    def _add(self, filename: str, match: BookMatch):
        self._exact.setdefault(self._key(filename), []).append(match)
        self._stem.setdefault(self._stem_key(filename), []).append(match)

    @staticmethod
    def _unique(values: list[BookMatch] | None, method: str) -> BookMatch | None:
        if not values:
            return None
        unique = {(row.book_id, row.format): row for row in values}
        if len(unique) != 1:
            return None
        row = next(iter(unique.values()))
        return BookMatch(row.book_id, row.format, row.filename, method)

    def match_filename(self, position_path: str) -> BookMatch | None:
        filename = os.path.basename(position_path)
        if filename.casefold().endswith(".po"):
            filename = filename[:-3]
        exact = self._unique(self._exact.get(self._key(filename)), "filename")
        if exact:
            return exact
        return self._unique(self._stem.get(self._stem_key(filename)), "filename_stem")

    def match_calibre_export_id(self, position_path: str) -> BookMatch | None:
        """Match Calibre's ``Title - Author (book_id).ext`` export names.

        The explicit database ID is only accepted when the filename also has
        Calibre's title/author separator and the referenced book owns the same
        format. This avoids treating arbitrary edition numbers as book IDs.
        """
        filename = os.path.basename(position_path)
        if filename.casefold().endswith(".po"):
            filename = filename[:-3]
        stem, extension = os.path.splitext(filename)
        if " - " not in stem or not extension:
            return None
        id_match = re.search(r"\((?P<book_id>\d+)\)\s*$", stem)
        if id_match is None:
            return None
        row = self._by_id_format.get((int(id_match.group("book_id")), extension[1:].upper()))
        if row is None:
            return None
        return BookMatch(row.book_id, row.format, row.filename, "calibre_id")


    def match_checksum(self, digest: str, size: int) -> BookMatch | None:
        matches: list[BookMatch] = []
        for match, path, expected_size in self._local_files:
            if expected_size not in (None, 0) and int(expected_size) != int(size):
                continue
            try:
                if os.path.getsize(path) != size:
                    continue
                local = hashlib.sha256()
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(256 * 1024), b""):
                        local.update(chunk)
                if local.hexdigest() == digest:
                    matches.append(match)
            except OSError:
                continue
        return self._unique(matches, "sha256")


def _extension(filename: str) -> str | None:
    suffix = os.path.splitext(str(filename or ""))[1].lstrip(".")
    return suffix.upper() if suffix else None


def get_or_create_settings(user_id: int, *, commit: bool = True):
    row = (ub.session.query(ub.MoonReaderWebdavSettings)
           .filter(ub.MoonReaderWebdavSettings.user_id == int(user_id)).first())
    if row is None:
        row = ub.MoonReaderWebdavSettings(
            user_id=int(user_id), enabled=False,
            base_url=DEFAULT_BASE_URL, username=DEFAULT_USERNAME,
            cache_path="", sync_status="idle",
        )
        ub.session.add(row)
        if commit:
            ub.session.commit()
    return row


def serialize_settings(row) -> dict[str, Any]:
    return {
        "enabled": bool(row.enabled),
        "base_url": row.base_url or DEFAULT_BASE_URL,
        "username": row.username or DEFAULT_USERNAME,
        "password_configured": bool(row.password_encrypted),
        "cache_path": row.cache_path or "",
        "last_test_at": _aware(row.last_test_at).isoformat() if row.last_test_at else None,
        "last_test_status": row.last_test_status,
        "last_test_error": row.last_test_error,
        "last_sync_at": _aware(row.last_sync_at).isoformat() if row.last_sync_at else None,
        "sync_status": row.sync_status or "idle",
        "last_sync_error": row.last_sync_error,
        "last_sync_summary": dict(row.last_sync_summary or {}),
    }


def test_connection(*, base_url: str, username: str, password: str, cache_path: str = "") -> dict[str, Any]:
    client = WebDavClient(base_url, username, password)
    try:
        root = client.propfind("", depth=0)
        if root is None:
            raise MoonReaderError("WebDAV root was not found.", code="not_found", status=404)
        detected, files, found = client.discover_positions(cache_path)
        return {
            "ok": True,
            "base_url": client.base_url,
            "cache_path": detected,
            "cache_found": found,
            "position_files": len(files),
        }
    finally:
        client.close()


def _same_or_newer(left: datetime | None, right: datetime | None) -> bool:
    left = _aware(left)
    right = _aware(right)
    if left is None:
        return False
    if right is None:
        return True
    return left >= right


def _apply_position(user, resource: WebDavResource, position: MoonPosition, match: BookMatch) -> str:
    existing = (ub.session.query(ub.MoonReaderProgress)
                .filter(ub.MoonReaderProgress.user_id == int(user.id),
                        ub.MoonReaderProgress.remote_path == resource.path)
                .first())
    if existing is not None and _same_or_newer(existing.moon_timestamp, position.timestamp):
        return "unchanged"

    if existing is None:
        existing = ub.MoonReaderProgress(
            user_id=int(user.id), book_id=match.book_id,
            remote_path=resource.path, raw_position=position.raw,
            percentage=position.percentage, moon_timestamp=position.timestamp,
        )
        ub.session.add(existing)
    existing.book_id = match.book_id
    existing.format = match.format
    existing.remote_etag = resource.etag
    existing.remote_modified = resource.modified
    existing.raw_position = position.raw
    existing.percentage = position.percentage
    existing.chapter = position.chapter
    existing.moon_timestamp = position.timestamp
    existing.synced_at = datetime.now(timezone.utc)

    from ..progress_syncing.models import KOSyncProgress
    from ..progress_syncing.protocols.kosync import (
        get_book_checksums,
        update_book_read_status,
    )

    device_id = f"moonreader-webdav:{int(user.id)}"
    document_keys = {str(match.book_id)}
    document_keys.update(str(value) for value in get_book_checksums(match.book_id) if value)
    newest = (ub.session.query(KOSyncProgress)
              .filter(KOSyncProgress.user_id == int(user.id),
                      KOSyncProgress.document.in_(tuple(document_keys)))
              .order_by(KOSyncProgress.timestamp.desc()).first())
    accepted = newest is None or not _same_or_newer(newest.timestamp, position.timestamp)
    if accepted:
        moon_progress = (ub.session.query(KOSyncProgress)
                         .filter(KOSyncProgress.user_id == int(user.id),
                                 KOSyncProgress.device_id == device_id,
                                 KOSyncProgress.document == str(match.book_id))
                         .order_by(KOSyncProgress.timestamp.desc()).first())
        if moon_progress is None:
            moon_progress = KOSyncProgress(
                user_id=int(user.id), document=str(match.book_id),
                progress=position.raw, percentage=position.percentage,
                device="Moon+ Reader", device_id=device_id,
                timestamp=position.timestamp,
            )
            ub.session.add(moon_progress)
        else:
            moon_progress.progress = position.raw
            moon_progress.percentage = position.percentage
            moon_progress.device = "Moon+ Reader"
            moon_progress.timestamp = position.timestamp
        update_book_read_status(user, match.book_id, position.percentage)
    ub.session.commit()
    return "updated" if accepted else "stored_only"


def sync_positions(user_id: int) -> dict[str, Any]:
    settings = get_or_create_settings(user_id)
    if not settings.enabled:
        raise MoonReaderError("Moon+ Reader sync is disabled.", code="sync_disabled")
    password = decrypt_password(settings.password_encrypted)
    client = WebDavClient(settings.base_url, settings.username, password)
    summary: dict[str, Any] = {
        "cache_path": None,
        "cache_found": False,
        "files_found": 0,
        "parsed": 0,
        "matched": 0,
        "updated": 0,
        "stored_only": 0,
        "unchanged": 0,
        "unmatched": [],
        "errors": [],
    }
    try:
        cache_path, files, cache_found = client.discover_positions(settings.cache_path or "")
        summary["cache_path"] = cache_path
        summary["cache_found"] = cache_found
        summary["files_found"] = len(files)
        if not files:
            return summary

        matcher = BookMatcher()
        user = ub.session.get(ub.User, int(user_id))
        if user is None:
            raise MoonReaderError("Moon+ Reader sync user no longer exists.", code="user_not_found", status=404)
        root_files = {os.path.basename(row.path).casefold(): row for row in client.root_files()}

        for resource in files:
            try:
                position = parse_position(client.get_bytes(resource.path))
                summary["parsed"] += 1
                match = matcher.match_filename(resource.path)
                if match is None:
                    match = matcher.match_calibre_export_id(resource.path)
                if match is None:
                    associated = os.path.basename(resource.path)[:-3]
                    remote_book = root_files.get(associated.casefold())
                    if remote_book is not None and (remote_book.size or 0) <= MAX_CHECKSUM_BOOK_BYTES:
                        digest, size = client.sha256(remote_book.path)
                        match = matcher.match_checksum(digest, size)
                if match is None:
                    if len(summary["unmatched"]) < MAX_SUMMARY_ITEMS:
                        summary["unmatched"].append(os.path.basename(resource.path))
                    continue
                summary["matched"] += 1
                outcome = _apply_position(user, resource, position, match)
                summary[outcome] += 1
            except MoonReaderError as exc:
                ub.session.rollback()
                if len(summary["errors"]) < MAX_SUMMARY_ITEMS:
                    summary["errors"].append({"file": os.path.basename(resource.path), "message": str(exc)})
            except Exception as exc:  # one malformed file must not abort the batch
                ub.session.rollback()
                log.exception("Moon+ Reader position import failed for %s", resource.path)
                if len(summary["errors"]) < MAX_SUMMARY_ITEMS:
                    summary["errors"].append({"file": os.path.basename(resource.path), "message": str(exc)})
        return summary
    finally:
        client.close()
