# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit-metadata endpoints for /api/v1.

Reuses cps/editbooks.py's edit_book_param — the canonical single-field editor
behind the legacy inline books-table editor — so every metadata write goes
through the same logic (directory restructuring on title/author change, tag/
series/language parsing, activity logging, commit/rollback). The SPA edit form
presents all fields together; we apply each changed field through that core.
"""
import json
from datetime import date

from flask import jsonify, request, Response
from flask_babel import get_locale
from sqlalchemy import text

from . import api_v1, log
from .serializers import serialize_book_detail
from .. import (
    calibre_db, config, db, deployment_profile, ub, isoLanguages,
    user_book_data,
)
from ..cw_login import current_user
from ..services.calibremcp_client import (
    CalibreMCPClientError,
    convert_book_format as mcp_convert_book_format,
    delete_book as mcp_delete_book,
    delete_book_format as mcp_delete_book_format,
    update_book_cover as mcp_update_book_cover,
    update_book_metadata as mcp_update_book_metadata,
)
from ..services.managed_cover import ManagedCoverError, stage_cover
from ..usermanagement import login_required_if_no_ano
import time

from ..editbooks import edit_book_param, delete_book_from_table, modify_identifiers
from ..helper import convert_book_format, save_cover, save_cover_from_url, tags_filters, get_convert_options

# Fields the SPA edit form can change, applied in this order. Title/authors come
# first because they may restructure the book's directory; the rest follow.
EDITABLE_FIELDS = [
    "title", "authors", "series", "series_index",
    "tags", "publishers", "languages", "comments", "rating", "pubdate",
]


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_edit():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_edit():
        return _err("forbidden", "You are not allowed to edit metadata", 403)
    return None


def _parse_edit_result(result):
    """edit_book_param returns a JSON Response, an empty string, or a
    ``(message, status)`` tuple. Normalize to ``(ok: bool, message: str)``."""
    if isinstance(result, Response):
        try:
            payload = json.loads(result.get_data(as_text=True) or "{}")
        except ValueError:
            return True, ""  # non-JSON success body
        if payload.get("success") is False:
            return False, payload.get("msg", "Update failed")
        return True, ""
    if isinstance(result, tuple):  # (message, status) — an error
        return False, str(result[0])
    return True, ""  # "" / None — success with no body


def _csv_items(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _managed_metadata_payload(data):
    """Normalize the existing SPA contract for CalibreMCP's shared helper."""
    payload = {}
    errors = {}

    if "title" in data:
        title = str(data.get("title") or "").strip()
        if title:
            payload["title"] = title
        else:
            errors["title"] = "Title cannot be empty."

    if "authors" in data:
        authors = [item.strip() for item in str(data.get("authors") or "").split(" & ") if item.strip()]
        if authors:
            payload["authors"] = authors
        else:
            errors["authors"] = "At least one author is required."

    for key in ("series", "comments"):
        if key in data:
            payload[key] = str(data.get(key) or "")
    if "tags" in data:
        payload["tags"] = _csv_items(data.get("tags"))
    if "publishers" in data:
        payload["publisher"] = str(data.get("publishers") or "").strip()

    if "series_index" in data:
        raw_index = data.get("series_index")
        if raw_index not in {None, ""}:
            try:
                series_index = float(raw_index)
                if series_index < 0:
                    raise ValueError
                payload["series_index"] = series_index
            except (TypeError, ValueError):
                errors["series_index"] = "Series index must be a non-negative number."

    if "rating" in data:
        try:
            rating = float(data.get("rating") or 0)
            if not 0 <= rating <= 5:
                raise ValueError
            payload["rating"] = rating
        except (TypeError, ValueError):
            errors["rating"] = "Rating must be between 0 and 5."

    if "languages" in data:
        language_names = _csv_items(data.get("languages"))
        localized = isoLanguages.get_language_names(get_locale()) or {}
        by_name = {str(name).strip().lower(): code for code, name in localized.items()}
        by_code = {str(code).strip().lower(): code for code in localized}
        language_codes = []
        unknown = []
        for language in language_names:
            key = language.strip().lower()
            code = by_name.get(key) or by_code.get(key)
            if code is None:
                unknown.append(language)
            elif code not in language_codes:
                language_codes.append(code)
        if unknown:
            errors["languages"] = "Invalid languages: " + ", ".join(unknown)
        else:
            payload["languages"] = language_codes

    if "pubdate" in data:
        pubdate = str(data.get("pubdate") or "").strip()
        if pubdate:
            try:
                date.fromisoformat(pubdate)
            except ValueError:
                errors["pubdate"] = "Publication date must use YYYY-MM-DD."
            else:
                payload["pubdate"] = pubdate
        else:
            payload["pubdate"] = ""

    if "identifiers" in data:
        identifiers = {}
        raw_identifiers = data.get("identifiers") or []
        if not isinstance(raw_identifiers, list):
            errors["identifiers"] = "Identifiers must be a list."
        else:
            for entry in raw_identifiers:
                if not isinstance(entry, dict):
                    continue
                id_type = str(entry.get("type") or "").strip().lower()
                id_value = str(entry.get("val") or "").strip()
                if not id_type or not id_value:
                    continue
                if id_type in identifiers:
                    errors["identifiers"] = "Duplicate identifier type — each type may appear once."
                    break
                identifiers[id_type] = id_value
            if "identifiers" not in errors:
                payload["identifiers"] = identifiers

    return payload, errors


def _ordered_language_codes(book_id):
    """Read Calibre's native language order; the CWNG ORM omits item_order."""
    rows = calibre_db.session.execute(
        text(
            "SELECT l.lang_code FROM books_languages_link AS link "
            "JOIN languages AS l ON l.id = link.lang_code "
            "WHERE link.book = :book_id ORDER BY link.item_order, link.id"
        ),
        {"book_id": int(book_id)},
    ).fetchall()
    return [row[0] for row in rows]


def _editable_metadata(book):
    """Current values for seeding the edit form (raw comments + rating included)."""
    comments = getattr(book, "comments", None) or []
    rating_rows = getattr(book, "ratings", None) or []
    languages = [
        isoLanguages.get_language_name(get_locale(), code)
        for code in _ordered_language_codes(book.id)
    ]
    # Publication date — sentinel year <= 101 (Books.DEFAULT_PUBDATE) reads as
    # "" so the editor's <input type="date"> shows blank for "no pubdate"
    # (mirrors serialize_book_detail's null mapping, #689).
    pubdate_raw = getattr(book, "pubdate", None)
    pubdate = (
        pubdate_raw.date().isoformat()
        if pubdate_raw is not None and getattr(pubdate_raw, "year", 0) > 101
        else ""
    )
    return {
        "id": book.id,
        "title": book.title or "",
        # calibre stores authors '|'-joined internally; present them '&'-joined
        # (the format edit_book_param's author handler expects back).
        "authors": " & ".join(a.name.replace("|", ",") for a in (book.authors or [])),
        "series": book.series[0].name if getattr(book, "series", None) else "",
        "series_index": book.series_index,
        "tags": ", ".join(t.name for t in (getattr(book, "tags", None) or [])),
        "publishers": ", ".join(p.name for p in (getattr(book, "publishers", None) or [])),
        "languages": ", ".join(languages),
        "comments": comments[0].text if comments else "",
        # calibre ratings are stored 0-10 (half-stars); expose 0-5.
        "rating": (rating_rows[0].rating / 2) if rating_rows else 0,
        # Publication date as YYYY-MM-DD (or "" for the default sentinel), #689.
        "pubdate": pubdate,
        # ISBN/ASIN/etc. — editable as a table in the SPA (fork #580).
        "identifiers": [
            {"type": i.type, "val": i.val}
            for i in (getattr(book, "identifiers", None) or [])
        ],
    }


@api_v1.route("/books/<int:book_id>/metadata")
@login_required_if_no_ano
def get_metadata(book_id):
    guard = _require_edit()
    if guard:
        return guard
    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True, allow_show_hidden=True)
    if not book:
        return _err("not_found", "Book not found", 404)
    return jsonify(_editable_metadata(book))


# Editor typeahead: existing-value suggestions per metadata field, so the SPA
# editor stops spawning near-duplicate tags/series/authors from typos
# (#741, #778, #689). Each field maps to the calibre model + name-normalization
# the legacy /get_*_json routes use; reusing calibre_db.get_typeahead keeps the
# SPA's suggestions identical to the classic editor's (single source of truth).
_TYPEAHEAD_MODELS = {
    "authors": (lambda: db.Authors, ("|", ",")),
    "publishers": (lambda: db.Publishers, ("|", ",")),
    "series": (lambda: db.Series, ("", "")),
}
_TYPEAHEAD_LIMIT = 25


def _typeahead_names(field, query):
    """Suggestion names for one editor metadata field, or None for an unknown
    field. Mirrors cps/web.py's typeahead routes field-for-field."""
    query = query or ""
    if field == "tags":
        raw = calibre_db.get_typeahead(db.Tags, query, tag_filter=tags_filters())
        return [row["name"] for row in json.loads(raw)][:_TYPEAHEAD_LIMIT]
    if field == "languages":
        # Localized display names, ranked start-matches-first (same as
        # /get_languages_json) but with the editor's larger result cap.
        needle = query.lower()
        names = list(isoLanguages.get_language_names(get_locale()).values())
        ranked = [s for s in names if s.lower().startswith(needle)]
        if len(ranked) < _TYPEAHEAD_LIMIT:
            seen = set(ranked)
            ranked.extend(s for s in names if needle in s.lower() and s not in seen)
        # de-dupe preserving the start-first order
        return list(dict.fromkeys(ranked))[:_TYPEAHEAD_LIMIT]
    spec = _TYPEAHEAD_MODELS.get(field)
    if spec is None:
        return None
    _model_factory, replace_chars = spec
    # Scope author/publisher/series suggestions to books visible to this user.
    # A global typeahead leaks metadata from denied languages/tags/custom columns.
    books = (calibre_db.session.query(db.Books)
             .filter(calibre_db.common_filters())
             .all())
    relation = {"authors": "authors", "publishers": "publishers", "series": "series"}[field]
    values = set()
    for book in books:
        for item in (getattr(book, relation, None) or []):
            value = str(getattr(item, "name", "") or "").strip()
            if not value:
                continue
            if replace_chars[0]:
                value = value.replace(replace_chars[0], replace_chars[1])
            values.add(value)
    needle = query.casefold()
    starts = sorted(value for value in values if value.casefold().startswith(needle))
    contains = sorted(
        value for value in values
        if needle in value.casefold() and value not in starts
    )
    return (starts + contains)[:_TYPEAHEAD_LIMIT]


@api_v1.route("/metadata/typeahead/<field>")
@login_required_if_no_ano
def metadata_typeahead(field):
    """Existing-value suggestions for an editor metadata field — one of tags,
    authors, series, publishers, languages. Read-only; gated on the edit role
    (only editors open the editor, and it avoids leaking the library's full
    tag/author list to non-editors). Reuses the legacy typeahead query so both
    editors agree on what already exists (#741, #778, #689)."""
    guard = _require_edit()
    if guard:
        return guard
    names = _typeahead_names(field, request.args.get("q", ""))
    if names is None:
        return _err("invalid_request", "Unknown typeahead field", 400)
    return jsonify({"field": field, "suggestions": names})


@api_v1.route("/books/<int:book_id>/metadata", methods=["POST"])
@login_required_if_no_ano
def update_metadata(book_id):
    guard = _require_edit()
    if guard:
        return guard
    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True, allow_show_hidden=True)
    if not book:
        return _err("not_found", "Book not found", 404)

    data = request.get_json(silent=True) or {}

    if deployment_profile.is_mcp_managed_library():
        payload, errors = _managed_metadata_payload(data)
        if errors:
            body = _editable_metadata(book)
            body["errors"] = errors
            return jsonify(body)
        if not payload:
            return jsonify(_editable_metadata(book))
        try:
            mcp_update_book_metadata(current_user.name, book_id, payload)
        except CalibreMCPClientError as exc:
            return _err("metadata_update_failed", str(exc), exc.status_code)

        # End the read transaction and expire ORM identity-map objects so the
        # response observes the external calibre-server commit immediately.
        calibre_db.session.rollback()
        calibre_db.session.expire_all()
        fresh = calibre_db.get_book(book_id)
        if not fresh:
            return _err("not_found", "Book not found after update", 404)
        return jsonify(_editable_metadata(fresh))

    errors = {}
    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        raw = data[field]
        value = "" if raw is None else str(raw)
        # edit_book_param reads vals['pk'] + vals['value']; checkA auto-syncs the
        # author sort key from the authors string (the inline-editor default).
        vals = {"pk": str(book_id), "value": value, "checkA": "true"}
        ok, message = _parse_edit_result(edit_book_param(field, vals))
        if not ok:
            errors[field] = message

    # Identifiers (ISBN/ASIN/…) — a list of {type, val}, reconciled against the
    # book's existing rows via the same helper the legacy editor uses (fork #580).
    if "identifiers" in data:
        raw_ids = data.get("identifiers") or []
        input_identifiers = []
        for entry in raw_ids if isinstance(raw_ids, list) else []:
            if not isinstance(entry, dict):
                continue
            id_type = str(entry.get("type") or "").strip().lower()
            id_val = str(entry.get("val") or "").strip()
            if not id_type or not id_val:
                continue  # skip blank rows (a half-filled row isn't an error)
            input_identifiers.append(db.Identifiers(id_val, id_type, book_id))
        changed, id_error = modify_identifiers(
            input_identifiers, book.identifiers, calibre_db.session)
        if id_error:
            # A duplicate type may have already queued partial add/deletes on the
            # session — discard them so a rejected payload never persists partially.
            calibre_db.session.rollback()
            errors["identifiers"] = "Duplicate identifier type — each type may appear once."
        elif changed:
            try:
                calibre_db.session.commit()
            except Exception as exc:  # noqa: BLE001 — surface as a field error, don't 500
                calibre_db.session.rollback()
                errors["identifiers"] = str(exc)

    # Re-fetch so the response reflects the committed state.
    fresh = calibre_db.get_book(book_id)
    body = _editable_metadata(fresh) if fresh else {}
    if errors:
        body["errors"] = errors
    return jsonify(body)


@api_v1.route("/books/<int:book_id>/delete", methods=["POST"])
@login_required_if_no_ano
def delete_book(book_id):
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_delete_books():
        return _err("forbidden", "You are not allowed to delete books", 403)
    # Authorize against the caller's VISIBLE library, not the raw table: a user
    # with the (global) delete role but a language/tag/custom-column visibility
    # restriction must not be able to enumerate and delete a book they cannot
    # see. allow_show_archived/hidden keep their OWN archived/hidden books
    # deletable (hidden is a listing exclusion, not an access revocation — #319).
    book = calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True
    )
    if not book:
        return _err("not_found", "Book not found", 404)
    if deployment_profile.is_mcp_managed_library():
        try:
            mcp_delete_book(current_user.name, book_id)
        except CalibreMCPClientError as exc:
            return _err("book_delete_failed", str(exc), exc.status_code)

        # The canonical book row/files are already gone through calibre-server.
        # Remove CWNG-owned per-user references in a separate app.db transaction.
        try:
            user_book_data.purge_user_book_data(
                book_id=book_id, remove_backup_files=False
            )
            ub.session.query(ub.BookOriginalFilename).filter(
                ub.BookOriginalFilename.book_id == book_id
            ).delete(synchronize_session=False)
            ub.session.commit()
        except Exception:
            ub.session.rollback()
            try:
                from pathlib import Path
                marker = Path("/root/calibre/CalibreWeb/var/run/reconcile.trigger")
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            except OSError:
                pass
            log.error(
                "Book %s deleted from Calibre but CWNG user-data cleanup failed",
                book_id,
                exc_info=True,
            )
            return _err(
                "app_cleanup_failed",
                "Book was deleted, but local user-state cleanup needs repair",
                500,
            )
        calibre_db.session.rollback()
        calibre_db.session.expire_all()
        return "", 204
    # delete_book_from_table re-checks the role and does the data-safe (DB-first,
    # files-last) whole-book delete + shelf cleanup. book_format="" = whole book.
    delete_book_from_table(book_id, "", True)
    return "", 204


@api_v1.route("/books/<int:book_id>/formats/<fmt>/delete", methods=["POST"])
@login_required_if_no_ano
def delete_format(book_id, fmt):
    """Delete a single format from a book (keeps the book). Reuses the data-safe
    delete core (re-checks role, DB-first/files-last)."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_delete_books():
        return _err("forbidden", "You are not allowed to delete books", 403)
    # Same visibility-scoped authorization as whole-book delete above.
    if not calibre_db.get_filtered_book(book_id, allow_show_archived=True, allow_show_hidden=True):
        return _err("not_found", "Book not found", 404)
    if deployment_profile.is_mcp_managed_library():
        try:
            mcp_delete_book_format(current_user.name, book_id, fmt.upper())
        except CalibreMCPClientError as exc:
            return _err("format_delete_failed", str(exc), exc.status_code)
        calibre_db.session.rollback()
        calibre_db.session.expire_all()
        return "", 204
    delete_book_from_table(book_id, fmt.upper(), True)
    return "", 204


@api_v1.route("/books/<int:book_id>/convert", methods=["POST"])
@login_required_if_no_ano
def convert_format(book_id):
    """Queue a format conversion. Body: {from, to}. Reuses helper.convert_book_format."""
    guard = _require_edit()
    if guard:
        return guard
    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True, allow_show_hidden=True)
    if not book:
        return _err("not_found", "Book not found", 404)
    data = request.get_json(silent=True) or {}
    src = (data.get("from") or "").strip().upper()
    dst = (data.get("to") or "").strip().upper()
    if not src or not dst:
        return _err("invalid_request", "Source and target formats are required", 400)
    if src == dst:
        return _err("invalid_request", "Source and target formats are the same", 400)
    allowed_sources, allowed_targets = get_convert_options(book)
    allowed_sources = [f.upper() for f in allowed_sources]
    allowed_targets = [f.upper() for f in allowed_targets]
    if src not in allowed_sources:
        return _err("invalid_request", "Source format is not valid for conversion", 400)
    if dst not in allowed_targets:
        return _err("invalid_request", "Target format is not valid for conversion", 400)
    if deployment_profile.is_mcp_managed_library():
        try:
            result = mcp_convert_book_format(
                current_user.name, book_id, src, dst
            )
        except CalibreMCPClientError as exc:
            return _err("convert_failed", str(exc), exc.status_code)
        calibre_db.session.rollback()
        calibre_db.session.expire_all()
        return jsonify({
            "ok": True,
            "message": "Converted %s to %s" % (src, dst),
            "output_path": result.get("output_path"),
        })
    rtn = convert_book_format(book_id, config.get_book_path(), src, dst, current_user.name)
    if rtn is None:
        return jsonify({"ok": True, "message": "Queued for conversion to %s" % dst})
    return _err("convert_failed", "There was an error converting this book: %s" % rtn, 400)


@api_v1.route("/books/<int:book_id>/cover", methods=["POST"])
@login_required_if_no_ano
def set_cover(book_id):
    """Replace a book's cover from an uploaded image (multipart `file`) or a
    remote URL (JSON {url}). Reuses helper.save_cover / save_cover_from_url so the
    size/format checks match the legacy edit page. The dedicated cover *picker*
    (provider candidate grid, e-reader padding preview) stays at /book/<id>/cover."""
    guard = _require_edit()
    if guard:
        return guard
    book = calibre_db.get_filtered_book(book_id)
    if not book:
        return _err("not_found", "Book not found", 404)

    if deployment_profile.is_mcp_managed_library():
        upload = request.files.get("file")
        data = request.get_json(silent=True) or {} if upload is None else {}
        staged = None
        try:
            staged = stage_cover(upload=upload, url=data.get("url"))
            mcp_update_book_cover(current_user.name, book_id, str(staged))
        except ManagedCoverError as exc:
            return _err("invalid_cover", str(exc), 400)
        except CalibreMCPClientError as exc:
            return _err("cover_update_failed", str(exc), exc.status_code)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

        calibre_db.session.rollback()
        calibre_db.session.expire_all()
        return jsonify({
            "ok": True,
            "cover_url": "/cover/%d/og?t=%d" % (book_id, int(time.time())),
        })

    if request.files.get("file"):
        ok, message = save_cover(request.files["file"], book.path)
    else:
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        if not url:
            return _err("invalid_request", "Provide an image file or a cover URL", 400)
        ok, message = save_cover_from_url(url, book.path)

    if ok:
        # Cache-bust so the browser refetches the replaced image immediately.
        return jsonify({"ok": True, "cover_url": "/cover/%d/og?t=%d" % (book_id, int(time.time()))})
    return _err("cover_failed", str(message), 400)
