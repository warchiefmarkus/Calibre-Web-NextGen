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
import heapq
import json
import os
import re
import unicodedata
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import selectinload

from .. import calibre_db, cli_param, config, config_sql, db, deployment_profile, logger, ub
from .moonreader_locator import (
    MoonLocatorError, MoonPosition, chapters_for_book, fraction_from_locator,
    infer_split_size, map_book_position, moon_split_texts,
    parse_position as parse_moon_position,
    serialize_position as serialize_moon_position,
)

log = logger.create()

DEFAULT_BASE_URL = "http://192.168.31.150:18283/books/"
DEFAULT_USERNAME = "reader"
DISCOVERY_MAX_DEPTH = 5
DISCOVERY_MAX_COLLECTIONS = 400
DISCOVERY_MAX_LOCATIONS = 50
DISCOVERY_PRIORITY_NAMES = frozenset({".moon+", "moon", "cache", "books", "apps"})
MAX_PROPFIND_BYTES = 5 * 1024 * 1024
MAX_POSITION_BYTES = 64 * 1024
MAX_CHECKSUM_BOOK_BYTES = 100 * 1024 * 1024
MAX_SUMMARY_ITEMS = 500
MOON_WRITE_FORMATS = frozenset({"FB2", "FBZ", "EPUB", "KEPUB", "PDF"})
_DAV = "{DAV:}"

class MoonReaderError(Exception):
    def __init__(self, message: str, *, code: str = "moonreader_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


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
    try:
        return parse_moon_position(value, max_bytes=MAX_POSITION_BYTES)
    except MoonLocatorError as exc:
        raise MoonReaderError(str(exc), code="invalid_position") from exc



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

    def put_bytes(self, relative_path: str, value: bytes, *, etag: str | None = None,
                  create_only: bool = False) -> None:
        if len(value) > MAX_POSITION_BYTES:
            raise MoonReaderError("Moon+ position file is too large.", code="invalid_position")
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        elif create_only:
            headers["If-None-Match"] = "*"
        response = self._request("PUT", relative_path, headers=headers, data=value)
        try:
            if response.status_code == 412:
                raise MoonReaderError(
                    "The Moon+ position changed while it was being synchronized.",
                    code="write_conflict", status=409,
                )
            if response.status_code not in {200, 201, 204}:
                raise MoonReaderError(
                    f"WebDAV PUT returned HTTP {response.status_code}.",
                    code="webdav_error", status=502,
                )
        finally:
            response.close()

    def resource_in_collection(self, collection: str, relative_path: str) -> WebDavResource | None:
        rows = self.propfind(collection, depth=1) or []
        wanted = normalize_cache_path(relative_path).casefold()
        return next((row for row in rows if row.path.casefold() == wanted), None)

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

    @staticmethod
    def _is_moon_cache_path(path: str) -> bool:
        parts = [part.casefold() for part in normalize_cache_path(path).split("/") if part]
        return len(parts) >= 2 and parts[-2:] == [".moon+", "cache"]

    @staticmethod
    def _discovery_priority(path: str) -> int:
        parts = [part.casefold() for part in normalize_cache_path(path).split("/") if part]
        if len(parts) >= 2 and parts[-2:] == [".moon+", "cache"]:
            return 0
        if parts and parts[-1] in DISCOVERY_PRIORITY_NAMES:
            return 1
        return 10

    def discover_cache_locations(
            self, *, max_depth: int = DISCOVERY_MAX_DEPTH,
            max_collections: int = DISCOVERY_MAX_COLLECTIONS,
            max_locations: int = DISCOVERY_MAX_LOCATIONS) -> dict[str, Any]:
        """Find Moon+ ``.Moon+/Cache`` collections with bounded WebDAV traversal.

        Likely Moon/Books/Apps paths are visited first so a large Calibre library
        does not make the useful locations wait behind hundreds of author folders.
        The limits are part of the public result so the UI can disclose a partial
        scan rather than pretending the whole WebDAV tree was searched.
        """
        max_depth = max(1, min(int(max_depth), 8))
        max_collections = max(1, min(int(max_collections), 1000))
        max_locations = max(1, min(int(max_locations), 100))
        queue: list[tuple[int, int, int, str]] = []
        serial = 0
        heapq.heappush(queue, (0, 0, serial, ""))
        seen: set[str] = set()
        locations: list[dict[str, Any]] = []
        scanned = 0
        truncated = False

        while queue:
            if scanned >= max_collections or len(locations) >= max_locations:
                truncated = True
                break
            _, depth, _, path = heapq.heappop(queue)
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            scanned += 1
            resources = self.propfind(path, depth=1)
            if resources is None:
                continue

            if self._is_moon_cache_path(path):
                files = [row for row in resources
                         if not row.is_collection and row.path.casefold().endswith(".po")]
                modified = max((_aware(row.modified) for row in files if row.modified), default=None)
                locations.append({
                    "path": path,
                    "position_files": len(files),
                    "last_modified": modified.isoformat() if modified else None,
                })
                continue

            if depth >= max_depth:
                continue
            children = [row.path for row in resources
                        if row.is_collection and row.path and row.path.casefold() != key
                        and row.path.rpartition("/")[0].casefold() == key]
            for child in sorted(set(children), key=lambda value: value.casefold()):
                child_key = child.casefold()
                if child_key in seen:
                    continue
                serial += 1
                heapq.heappush(queue, (self._discovery_priority(child), depth + 1, serial, child))

        locations.sort(key=lambda row: str(row["path"]).casefold())
        return {
            "locations": locations,
            "scanned_collections": scanned,
            "max_depth": max_depth,
            "max_collections": max_collections,
            "truncated": truncated,
        }

    def discover_positions(self, cache_path: str = "") -> tuple[str, list[WebDavResource], bool]:
        candidate = normalize_cache_path(cache_path)
        if not candidate:
            return "", [], False
        resources = self.propfind(candidate, depth=1)
        if resources is None:
            return candidate, [], False
        files = [row for row in resources if not row.is_collection and row.path.casefold().endswith(".po")]
        return candidate, files, True

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


    def for_book(self, book_id: int, fmt: str | None = None) -> BookMatch | None:
        target = int(book_id)
        if fmt:
            row = self._by_id_format.get((target, str(fmt).upper()))
            if row is not None:
                return BookMatch(row.book_id, row.format, row.filename, "book_id")
        rows = [row for (value, _), row in self._by_id_format.items() if value == target]
        if not rows:
            return None
        preferred = sorted(rows, key=lambda row: (
            {"FB2": 0, "EPUB": 1, "KEPUB": 2, "PDF": 3}.get(str(row.format), 9),
            row.filename.casefold(),
        ))[0]
        return BookMatch(preferred.book_id, preferred.format, preferred.filename, "book_id")

    def local_path(self, match: BookMatch) -> str | None:
        for candidate, path, _ in self._local_files:
            if candidate.book_id == match.book_id and candidate.format == match.format:
                return path
        return None

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
            "selection_required": not bool(detected),
        }
    finally:
        client.close()


def find_cache_locations(*, base_url: str, username: str, password: str) -> dict[str, Any]:
    client = WebDavClient(base_url, username, password)
    try:
        root = client.propfind("", depth=0)
        if root is None:
            raise MoonReaderError("WebDAV root was not found.", code="not_found", status=404)
        result = client.discover_cache_locations()
        result.update({"ok": True, "base_url": client.base_url})
        return result
    finally:
        client.close()


def moon_device_id(user_id: int) -> str:
    """Stable numeric device id accepted by Moon+'s self-echo comparison."""
    key, error = config_sql.get_encryption_key(os.path.dirname(cli_param.settings_path))
    if error:
        raise MoonReaderError("Could not access the server encryption key.",
                              code="encryption_unavailable", status=500)
    digest = hashlib.sha256(bytes(key) + f":moonreader:{int(user_id)}".encode()).digest()
    return str(1_000_000_000_000 + int.from_bytes(digest[:8], "big") % 8_000_000_000_000)


def _same_or_newer(left: datetime | None, right: datetime | None) -> bool:
    left = _aware(left)
    right = _aware(right)
    if left is None:
        return False
    if right is None:
        return True
    return left >= right


def _latest_native_position(payload: Any) -> dict[str, Any] | None:
    positions = payload.get("positions", []) if isinstance(payload, dict) else []
    rows = [row for row in positions if isinstance(row, dict)]
    if not rows:
        return None
    return max(rows, key=lambda row: float(row.get("epoch") or 0))


def _native_modified(native: dict[str, Any] | None) -> datetime | None:
    if not native:
        return None
    value = float(native.get("epoch") or 0)
    return datetime.fromtimestamp(value, tz=timezone.utc) if value > 0 else None


def _native_fraction(native: dict[str, Any] | None) -> float:
    return max(0.0, min(1.0, float((native or {}).get("pos_frac") or 0)))


def _native_locator(position: MoonPosition, fmt: str) -> str:
    if str(fmt or "").upper() == "PDF":
        return json.dumps({
            "type": "pdf-position", "version": 1,
            "page": max(1, int(position.chapter)),
            "scroll": {"x": 0, "y": 0},
        }, separators=(",", ":"))
    return "epubcfi(/6/2!/4/2)"


def _pdf_page(native: dict[str, Any] | None) -> int | None:
    value = (native or {}).get("cfi")
    if not value:
        return None
    try:
        payload = json.loads(value)
        if isinstance(payload, dict) and "page" in payload:
            return int(payload["page"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


def _remote_fraction(position: MoonPosition, match: BookMatch,
                     matcher: BookMatcher) -> float:
    stored = max(0.0, min(1.0, float(position.percentage) / 100.0))
    path = matcher.local_path(match)
    if not path or position.split_index is None:
        return stored
    try:
        chapters = chapters_for_book(path, match.format or "")
        current = next(
            (item for item in chapters if item.index == position.chapter), None,
        )
        if current is None:
            return stored
        split_size = infer_split_size(chapters, position)
        splits = moon_split_texts(current, split_size)
        if not 0 <= position.split_index < len(splits):
            return stored
        exact = fraction_from_locator(
            chapters, position.chapter, position.offset,
            split_index=position.split_index, split_size=split_size,
        )
        # T.getPercentStr2 stores one decimal. Accept the structural locator
        # only when it rounds to the payload value; otherwise the local file or
        # inferred device split profile differs and percentage is safer.
        if abs((exact * 100.0) - position.percentage) <= 0.051:
            return exact
    except (MoonLocatorError, OSError):
        log.warning(
            "Could not map Moon locator for book %s; using stored percentage",
            match.book_id,
        )
    return stored


def _record_progress(user, resource: WebDavResource, position: MoonPosition,
                     match: BookMatch, *, native_epoch: float | None,
                     direction: str) -> None:
    row = (ub.session.query(ub.MoonReaderProgress)
           .filter(ub.MoonReaderProgress.user_id == int(user.id),
                   ub.MoonReaderProgress.remote_path == resource.path).first())
    modified = _aware(resource.modified) or datetime.now(timezone.utc)
    if row is None:
        row = ub.MoonReaderProgress(
            user_id=int(user.id), book_id=match.book_id,
            remote_path=resource.path, raw_position=position.raw,
            percentage=position.percentage, moon_timestamp=modified,
        )
        ub.session.add(row)
    row.book_id = match.book_id
    row.format = match.format
    row.remote_etag = resource.etag
    row.remote_modified = resource.modified
    row.remote_device_id = position.device_id
    row.raw_position = position.raw
    row.percentage = position.percentage
    row.chapter = position.chapter
    row.split_index = position.split_index
    row.character_offset = position.offset
    row.moon_timestamp = modified
    row.last_native_epoch = native_epoch
    row.last_direction = direction
    row.synced_at = datetime.now(timezone.utc)


def _update_legacy_progress(user, match: BookMatch, position: MoonPosition,
                            modified: datetime | None) -> None:
    from ..progress_syncing.models import KOSyncProgress
    from ..progress_syncing.protocols.kosync import update_book_read_status

    device_id = f"moonreader-webdav:{int(user.id)}"
    row = (ub.session.query(KOSyncProgress)
           .filter(KOSyncProgress.user_id == int(user.id),
                   KOSyncProgress.device_id == device_id,
                   KOSyncProgress.document == str(match.book_id))
           .order_by(KOSyncProgress.timestamp.desc()).first())
    timestamp = _aware(modified) or datetime.now(timezone.utc)
    if row is None:
        row = KOSyncProgress(
            user_id=int(user.id), document=str(match.book_id),
            progress=position.raw, percentage=position.percentage,
            device="Moon+ Reader", device_id=device_id, timestamp=timestamp,
        )
        ub.session.add(row)
    else:
        row.progress = position.raw
        row.percentage = position.percentage
        row.device = "Moon+ Reader"
        row.timestamp = timestamp
    update_book_read_status(user, match.book_id, position.percentage)


def _import_remote(user, resource: WebDavResource, position: MoonPosition,
                   match: BookMatch, matcher: BookMatcher,
                   native: dict[str, Any] | None) -> str:
    from .calibremcp_client import set_reader_position

    fraction = _remote_fraction(position, match, matcher)
    if deployment_profile.use_calibre_native_reader_data():
        set_reader_position(
            str(user.name), match.book_id, match.format or "",
            cfi=_native_locator(position, match.format or ""),
            position_fraction=fraction,
            device=f"moonreader-webdav:{int(user.id)}",
        )
    _update_legacy_progress(user, match, position, resource.modified)
    _record_progress(
        user, resource, position, match,
        native_epoch=float((native or {}).get("epoch") or 0) or None,
        direction="from_moon",
    )
    ub.session.commit()
    return "downloaded"


def _remote_path(cache_path: str, match: BookMatch,
                 existing: Any | None = None) -> str:
    selected_cache = normalize_cache_path(cache_path)
    if existing is not None and existing.remote_path:
        tracked_path = normalize_cache_path(existing.remote_path)
        tracked_parent = normalize_cache_path(os.path.dirname(tracked_path))
        # Tracking survives cache-folder changes. Never resurrect an obsolete
        # WebDAV path (for example legacy `.Moon+/Cache`) after the user selected
        # `Moon/.Moon+/Cache`; SFTPGo correctly rejects a PUT when that old
        # parent no longer exists.
        if tracked_parent.casefold() == selected_cache.casefold():
            return tracked_path
    filename = os.path.basename(match.filename)
    return normalize_cache_path(f"{selected_cache}/{filename}.po")


def _export_native(user, client: WebDavClient, cache_path: str,
                   resource: WebDavResource | None, match: BookMatch,
                   matcher: BookMatcher, native: dict[str, Any],
                   *, anchor_text: str | None = None,
                   remote_position: MoonPosition | None = None) -> str:
    path = matcher.local_path(match)
    fraction = _native_fraction(native)
    try:
        mapped = map_book_position(
            path or "", match.format or "", fraction,
            anchor_text=anchor_text, page=_pdf_page(native),
            remote_position=remote_position,
        )
    except (MoonLocatorError, OSError):
        mapped = map_book_position("", match.format or "", fraction,
                                   page=_pdf_page(native))
    raw = serialize_moon_position(
        device_id=moon_device_id(int(user.id)), chapter=mapped.chapter,
        split_index=mapped.split_index, offset=mapped.offset,
        percentage=mapped.percentage,
    )
    if resource is not None:
        # Reconciliation is path-specific. When multiple Moon files match the
        # same book, never redirect this write through an arbitrary tracking row
        # for a sibling filename.
        remote_path = normalize_cache_path(resource.path)
    else:
        existing = (ub.session.query(ub.MoonReaderProgress)
                    .filter(ub.MoonReaderProgress.user_id == int(user.id),
                            ub.MoonReaderProgress.book_id == match.book_id,
                            ub.MoonReaderProgress.format == match.format).first())
        remote_path = _remote_path(cache_path, match, existing)
    client.put_bytes(
        remote_path, raw.encode("utf-8"),
        etag=resource.etag if resource else None,
        create_only=resource is None,
    )
    fresh = client.resource_in_collection(cache_path, remote_path) or WebDavResource(
        remote_path, False, size=len(raw), modified=datetime.now(timezone.utc))
    position = parse_position(raw)
    _record_progress(
        user, fresh, position, match,
        native_epoch=float(native.get("epoch") or 0) or None,
        direction="to_moon",
    )
    ub.session.commit()
    return "uploaded_anchor" if mapped.matched_anchor else "uploaded"


def _is_bootstrap_zero(position: MoonPosition | None,
                       native: dict[str, Any] | None,
                       server_device: str, tracking: Any | None = None) -> bool:
    if position is None or native is None:
        return False
    remote_fraction = max(0.0, min(1.0, position.percentage / 100.0))
    native_fraction = _native_fraction(native)
    if not (remote_fraction <= 0 < native_fraction):
        return False
    tracked_fraction = max(
        0.0,
        min(1.0, float(getattr(tracking, "percentage", 0) or 0) / 100.0),
    )
    tracked_device = str(getattr(tracking, "remote_device_id", "") or "")
    # Legacy tracking rows did not retain Moon's device id. Prefer the non-zero
    # native state over a destructive zero until a real Moon-origin device has
    # been observed for this path.
    return bool(
        tracking is None
        or (tracked_fraction > 0 and not tracked_device)
        or (tracked_fraction > 0 and tracked_device == server_device)
    )


def _conflict_direction(resource: WebDavResource | None, position: MoonPosition | None,
                        native: dict[str, Any] | None, server_device: str,
                        tracking: Any | None = None) -> str:
    """Return from_moon/to_moon/unchanged; Moon wins real ties and races.

    A newly observed Moon+ file at exactly 0% is special. Moon creates that
    bootstrap locator when a book is opened for the first time, before WebDAV
    has necessarily supplied an existing position. Its fresh mtime therefore
    does *not* prove that the user intentionally reset a non-zero Calibre
    position. For an unseen file (or one previously seeded by this server), a
    non-zero native position wins over that technical zero. Once we have
    accepted a genuine Moon-origin state for the file, a later 0% can still be
    an intentional reset and falls through to the normal timestamp policy.
    """
    if resource is None or position is None:
        return "to_moon" if native else "unchanged"
    if native is None:
        return "from_moon"
    remote_fraction = max(0.0, min(1.0, position.percentage / 100.0))
    native_fraction = _native_fraction(native)
    if _is_bootstrap_zero(position, native, server_device, tracking):
        return "to_moon"
    same = abs(remote_fraction - native_fraction) < 0.0005
    native_device = str(native.get("device") or "")
    if same and (position.device_id == server_device or
                 native_device.startswith("moonreader-webdav:")):
        return "unchanged"
    if native_fraction <= 0 < remote_fraction:
        return "from_moon"
    remote_time = _aware(resource.modified)
    native_time = _native_modified(native)
    if remote_time is None:
        return "from_moon"
    if native_time is None:
        return "from_moon"
    # Filesystems and Calibre may round timestamps differently. Moon is the
    # primary reader, so it wins ties and the two-second uncertainty window.
    if remote_time.timestamp() >= native_time.timestamp() - 2.0:
        return "from_moon"
    return "to_moon"


def _match_remote(matcher: BookMatcher, resource: WebDavResource,
                  client: WebDavClient, root_files: dict[str, WebDavResource]) -> BookMatch | None:
    match = matcher.match_filename(resource.path) or matcher.match_calibre_export_id(resource.path)
    if match is not None:
        return match
    associated = os.path.basename(resource.path)[:-3]
    remote_book = root_files.get(associated.casefold())
    if remote_book is not None and (remote_book.size or 0) <= MAX_CHECKSUM_BOOK_BYTES:
        digest, size = client.sha256(remote_book.path)
        return matcher.match_checksum(digest, size)
    return None


def _native_pairs(user_name: str) -> set[tuple[int, str]]:
    if not deployment_profile.use_calibre_native_reader_data():
        return set()
    # Keep bulk enumeration in the exact same Calibre reader namespace used by
    # the catalog progress adapter and CalibreMCP. CWNG's visible user may be
    # ``admin`` while Calibre stores the native row as ``cwng-admin``. Querying
    # by the visible name silently drops every native-only position and prevents
    # proactive Moon+ .po creation.
    from .reading_progress import _native_reader_username

    native_user = _native_reader_username(user_name)
    if not native_user:
        return set()
    path = os.path.join(config.config_calibre_dir, "metadata.db")
    try:
        import sqlite3
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            return {
                (int(row[0]), str(row[1]).upper())
                for row in connection.execute(
                    "SELECT DISTINCT book, format FROM last_read_positions WHERE user = ?",
                    (native_user,),
                )
            }
    except Exception:
        log.exception("Could not enumerate native Calibre reading positions")
        return set()


def reconcile_book(user, client: WebDavClient, cache_path: str,
                   matcher: BookMatcher, match: BookMatch,
                   resource: WebDavResource | None,
                   *, anchor_text: str | None = None,
                   retry_conflict: bool = True) -> str:
    from .calibremcp_client import get_reader_position

    position = parse_position(client.get_bytes(resource.path)) if resource else None
    native = _latest_native_position(
        get_reader_position(str(user.name), match.book_id, match.format or "")
    ) if deployment_profile.use_calibre_native_reader_data() else None
    server_device = moon_device_id(int(user.id))
    tracking = None
    if resource is not None:
        tracking = (ub.session.query(ub.MoonReaderProgress)
                    .filter(ub.MoonReaderProgress.user_id == int(user.id),
                            ub.MoonReaderProgress.remote_path == resource.path).first())
    native_epoch = float((native or {}).get("epoch") or 0)
    own_write_unchanged = bool(
        position is not None and position.device_id == server_device and
        tracking is not None and tracking.last_direction == "to_moon" and
        tracking.remote_etag == resource.etag and
        native_epoch <= float(tracking.last_native_epoch or 0) + 0.01
    )
    direction = "unchanged" if own_write_unchanged else _conflict_direction(
        resource, position, native, server_device, tracking)
    if direction == "unchanged":
        if resource and position:
            _record_progress(
                user, resource, position, match,
                native_epoch=float((native or {}).get("epoch") or 0) or None,
                direction="unchanged",
            )
            ub.session.commit()
        return "unchanged"
    if direction == "from_moon" and resource and position:
        return _import_remote(user, resource, position, match, matcher, native)
    if direction == "to_moon" and native:
        if str(match.format or "").upper() not in MOON_WRITE_FORMATS:
            return "deferred"
        native_device = str(native.get("device") or "")
        # Older CWNG rows have an exact CFI but no text anchor. A raw Foliate
        # fraction is not Moon's fraction and can move Moon by thousands of
        # characters (2.7% Foliate vs 2.4% Moon in Console Wars). Wait for the
        # next real relocate event, which carries visible text, rather than
        # damaging an existing Moon file. External Calibre devices have no
        # anchor channel and therefore retain the structural fraction fallback.
        bootstrap_zero = _is_bootstrap_zero(
            position, native, server_device, tracking,
        )
        if (resource is not None and native_device.startswith("cwng-web")
                and not anchor_text and not bootstrap_zero):
            return "deferred"
        try:
            return _export_native(
                user, client, cache_path, resource, match, matcher, native,
                anchor_text=anchor_text, remote_position=position,
            )
        except MoonReaderError as exc:
            if exc.code != "write_conflict" or not retry_conflict:
                raise
            fresh = client.resource_in_collection(
                cache_path, resource.path if resource else _remote_path(cache_path, match))
            return reconcile_book(
                user, client, cache_path, matcher, match, fresh,
                anchor_text=anchor_text, retry_conflict=False,
            )
    return "unchanged"


def sync_positions(user_id: int, *, book_id: int | None = None,
                   fmt: str | None = None, anchor_text: str | None = None,
                   include_native_only: bool = True) -> dict[str, Any]:
    """Reconcile Moon WebDAV and Calibre positions in both directions."""
    settings = get_or_create_settings(user_id)
    if not settings.enabled:
        raise MoonReaderError("Moon+ Reader sync is disabled.", code="sync_disabled")
    cache_path = normalize_cache_path(settings.cache_path or "")
    if not cache_path:
        raise MoonReaderError("Find and select a Moon+ sync folder before synchronizing.",
                              code="cache_path_required")
    client = WebDavClient(settings.base_url, settings.username,
                          decrypt_password(settings.password_encrypted))
    summary: dict[str, Any] = {
        "cache_path": cache_path, "cache_found": False, "files_found": 0,
        "parsed": 0, "matched": 0, "updated": 0, "uploaded": 0,
        "downloaded": 0, "stored_only": 0, "unchanged": 0, "deferred": 0,
        "unmatched": [], "errors": [],
    }
    try:
        _, files, found = client.discover_positions(cache_path)
        summary["cache_found"] = found
        summary["files_found"] = len(files)
        matcher = BookMatcher()
        user = ub.session.get(ub.User, int(user_id))
        if user is None:
            raise MoonReaderError("Moon+ Reader sync user no longer exists.",
                                  code="user_not_found", status=404)
        root_files = {os.path.basename(row.path).casefold(): row for row in client.root_files()}
        remote_by_key: dict[
            tuple[int, str], list[tuple[WebDavResource, BookMatch]]
        ] = {}
        for resource in files:
            try:
                match = _match_remote(matcher, resource, client, root_files)
                if match is None:
                    if len(summary["unmatched"]) < MAX_SUMMARY_ITEMS:
                        summary["unmatched"].append(os.path.basename(resource.path))
                    continue
                summary["matched"] += 1
                key = (match.book_id, str(match.format or "").upper())
                remote_by_key.setdefault(key, []).append((resource, match))
            except Exception as exc:
                if len(summary["errors"]) < MAX_SUMMARY_ITEMS:
                    summary["errors"].append({"file": os.path.basename(resource.path),
                                              "message": str(exc)})

        keys = set(remote_by_key)
        if book_id is not None:
            selected = matcher.for_book(int(book_id), fmt)
            keys = {(selected.book_id, str(selected.format or "").upper())} if selected else set()
        elif include_native_only:
            keys.update(_native_pairs(str(user.name)))

        for key in sorted(keys):
            resource_matches = remote_by_key.get(key) or []
            if resource_matches:
                # A book can legitimately acquire multiple Moon position files
                # after filename/export changes. Reconcile every candidate, from
                # oldest to newest, so a fresh real Moon update gets the final
                # say while a fresh first-open 0% is repaired by the bootstrap
                # policy instead of being silently skipped by dict overwrite.
                work = sorted(
                    resource_matches,
                    key=lambda item: (
                        _aware(item[0].modified).timestamp()
                        if _aware(item[0].modified) is not None else float("-inf"),
                        item[0].path.casefold(),
                    ),
                )
            else:
                match = matcher.for_book(*key)
                work = [(None, match)] if match is not None else []

            for resource, match in work:
                if match is None:
                    continue
                try:
                    outcome = reconcile_book(
                        user, client, cache_path, matcher, match, resource,
                        anchor_text=anchor_text if book_id == match.book_id else None,
                    )
                    if outcome.startswith("uploaded"):
                        summary["uploaded"] += 1
                        summary["updated"] += 1
                    elif outcome == "downloaded":
                        summary["downloaded"] += 1
                        summary["updated"] += 1
                        summary["parsed"] += 1
                    else:
                        summary[outcome] = summary.get(outcome, 0) + 1
                except MoonReaderError as exc:
                    ub.session.rollback()
                    if len(summary["errors"]) < MAX_SUMMARY_ITEMS:
                        summary["errors"].append({
                            "file": os.path.basename(resource.path) if resource else match.filename,
                            "message": str(exc),
                        })
                except Exception as exc:
                    ub.session.rollback()
                    log.exception("Moon+ reconciliation failed for book %s", match.book_id)
                    if len(summary["errors"]) < MAX_SUMMARY_ITEMS:
                        summary["errors"].append({"file": match.filename, "message": str(exc)})
        return summary
    finally:
        client.close()
