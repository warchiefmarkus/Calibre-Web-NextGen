# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Book-upload endpoint for /api/v1.

Queues new book files into the CWA ingest folder using the same helpers the
legacy /upload route uses (_validate_uploaded_file, _get_ingest_path,
_save_to_ingest_atomic_rename, _ensure_ingest_dir_writable) — so validation,
atomic placement and the ingest hand-off stay single-sourced. The ingest
service then imports the files into the calibre library. Returns JSON with the
queued filenames and any per-file errors (the legacy route only flashed those).
"""
import os
import json
import hashlib

from flask import jsonify, request
from flask_babel import lazy_gettext as N_
from markupsafe import escape

from . import api_v1, log
from .. import config, calibre_db, deployment_profile, ub
from ..config_sql import uploads_enabled
from ..cw_login import current_user
from ..services.calibremcp_client import (
    CalibreMCPClientError,
    add_book_format as mcp_add_book_format,
    import_book as mcp_import_book,
)
from ..services.managed_format import ManagedFormatError, stage_uploaded_format
from ..usermanagement import login_required_if_no_ano
from ..services.worker import WorkerThread
from ..tasks.upload import TaskUpload
from ..editbooks import (
    _validate_uploaded_file,
    _get_ingest_path,
    _save_to_ingest_atomic_rename,
    _ensure_ingest_dir_writable,
)


def _file_idempotency_key(path, user_name, original_filename):
    digest = hashlib.sha256()
    digest.update(str(user_name).encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(original_filename).encode("utf-8"))
    digest.update(b"\0")
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "cwng-import-" + digest.hexdigest()


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _record_original_filename(book_id, original_filename):
    """Insert the import filename once; idempotent replays are a no-op."""
    original = ub.session.get(ub.BookOriginalFilename, book_id)
    if original is None:
        ub.session.add(ub.BookOriginalFilename(
            book_id=book_id,
            filename=original_filename,
        ))
        ub.session.commit()
        return "created"
    if original.filename != original_filename:
        log.warning(
            "Book %s already has original filename %r; keeping it instead of %r",
            book_id,
            original.filename,
            original_filename,
        )
        return "kept_existing"
    return "unchanged"


def _uploads_disabled():
    """Return a consistent refusal when the admin disabled uploads."""
    if uploads_enabled(config):
        return None
    return _err("uploads_disabled",
                "Uploading is disabled on this server", 403)


@api_v1.route("/upload", methods=["POST"])
@login_required_if_no_ano
def upload_books():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_upload():
        return _err("forbidden", "You are not allowed to upload books", 403)
    disabled = _uploads_disabled()
    if disabled:
        return disabled

    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        return _err("invalid_request", "No files were uploaded", 400)

    if deployment_profile.is_mcp_managed_library():
        queued, errors, imported = [], [], []
        for uploaded in files:
            if not _validate_uploaded_file(uploaded):
                errors.append({
                    "filename": uploaded.filename,
                    "error": "File type not allowed (allowed: {})".format(
                        config.config_upload_formats
                    ),
                })
                continue
            staged = None
            try:
                uploaded.stream.seek(0)
                staged = stage_uploaded_format(uploaded)
                original_filename = os.path.basename(
                    uploaded.filename.replace("\\", "/")
                )
                result = mcp_import_book(
                    current_user.name, str(staged), original_filename,
                    idempotency_key=_file_idempotency_key(
                        staged, current_user.name, original_filename
                    ),
                )
                book_id = int(result["book_id"])
                queued.append(uploaded.filename)
                imported.append({
                    "filename": uploaded.filename,
                    "book_id": book_id,
                })
                try:
                    _record_original_filename(book_id, original_filename)
                except Exception:
                    ub.session.rollback()
                    log.error(
                        "Book %s imported, but original filename could not be saved",
                        book_id,
                        exc_info=True,
                    )
                    errors.append({
                        "filename": uploaded.filename,
                        "error": "Book imported, but original filename was not recorded",
                    })
            except (ManagedFormatError, CalibreMCPClientError) as exc:
                errors.append({"filename": uploaded.filename, "error": str(exc)})
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
        calibre_db.session.rollback()
        calibre_db.session.expire_all()
        if imported:
            from ..tasks.external_ratings import queue_external_rating_refresh
            hardcover_tokens = []
            for raw_token in (
                getattr(current_user, "hardcover_token", None),
                config.resolved_hardcover_token(),
            ):
                token = str(raw_token or "").replace("Bearer ", "", 1).strip()
                if token and token not in hardcover_tokens:
                    hardcover_tokens.append(token)
            queue_external_rating_refresh(
                [item["book_id"] for item in imported],
                username=current_user.name,
                hardcover_tokens=hardcover_tokens,
                google_books_api_key=(
                    str(getattr(config, "config_google_books_api_key", None) or "").strip()
                    or None
                ),
            )
        return jsonify({
            "queued": queued,
            "errors": errors,
            "imported": imported,
            "completed": True,
        })

    try:
        _ensure_ingest_dir_writable(allow_create=True, check_write=False)
    except PermissionError as e:
        log.error("Ingest directory not writable: %s", e)
        return _err("ingest_unwritable",
                    "The ingest folder is not writable; check the /cwa-book-ingest volume", 500)

    allowed = config.config_upload_formats
    queued, errors = [], []
    for uploaded in files:
        if not _validate_uploaded_file(uploaded):
            errors.append({"filename": uploaded.filename,
                           "error": "File type not allowed (allowed: {})".format(allowed)})
            continue
        try:
            final_path = _get_ingest_path(uploaded, prefix_parts=["new", current_user.id])
            tmp_path, final_path = _save_to_ingest_atomic_rename(uploaded, final_path)
            # The watched filename is deliberately prefixed for collision-free
            # staging. Carry the browser-selected basename explicitly so ingest
            # never has to guess which part of that internal name is user data.
            with open(final_path + ".cwa.json", "w", encoding="utf-8") as mf:
                json.dump({"action": "import", "original_filename": uploaded.filename},
                          mf, ensure_ascii=False)
            # The atomic rename into the watched ingest dir is what triggers import.
            os.replace(tmp_path, final_path)
            WorkerThread.add(current_user.name,
                             TaskUpload(N_("Upload done, processing, please wait..."),
                                        escape(uploaded.filename)))
            queued.append(uploaded.filename)
        except Exception as e:  # noqa: BLE001 — report per-file, keep going
            log.error_or_exception("Failed to queue upload for ingest: {}".format(e))
            errors.append({"filename": uploaded.filename, "error": "Failed to queue for processing"})

    return jsonify({"queued": queued, "errors": errors})


@api_v1.route("/books/<int:book_id>/formats", methods=["POST"])
@login_required_if_no_ano
def add_format(book_id):
    """Add a format (file) to an existing book. Mirrors the legacy
    do_edit_book btn-upload-format path: drop the file into the ingest folder
    with an ``add_format`` sidecar manifest; the ingest service attaches it to
    the book. Single-sourced via the same ingest helpers as /upload."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_upload():
        return _err("forbidden", "You are not allowed to upload books", 403)
    disabled = _uploads_disabled()
    if disabled:
        return disabled
    if not calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True
    ):
        return _err("not_found", "Book not found", 404)

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return _err("invalid_request", "No file was uploaded", 400)
    if not _validate_uploaded_file(uploaded):
        return _err("invalid_request",
                    "File type not allowed (allowed: {})".format(config.config_upload_formats), 400)

    if deployment_profile.is_mcp_managed_library():
        staged = None
        try:
            staged = stage_uploaded_format(uploaded)
            result = mcp_add_book_format(current_user.name, book_id, str(staged))
        except ManagedFormatError as exc:
            return _err("invalid_format", str(exc), 400)
        except CalibreMCPClientError as exc:
            return _err("format_add_failed", str(exc), exc.status_code)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

        calibre_db.session.rollback()
        calibre_db.session.expire_all()
        return jsonify({
            "queued": uploaded.filename,
            "completed": True,
            "formats": [item.get("format") for item in result.get("formats", [])],
        })

    try:
        _ensure_ingest_dir_writable(allow_create=True, check_write=False)
        final_path = _get_ingest_path(uploaded, prefix_parts=["format", book_id])
        tmp_path, final_path = _save_to_ingest_atomic_rename(uploaded, final_path)
        # Sidecar manifest tells the ingest service to attach this as a new
        # format on book_id rather than import it as a new book.
        with open(final_path + ".cwa.json", "w", encoding="utf-8") as mf:
            json.dump({"action": "add_format", "book_id": book_id,
                       "original_filename": uploaded.filename}, mf, ensure_ascii=False)
        os.replace(tmp_path, final_path)  # atomic move triggers ingest
        WorkerThread.add(current_user.name,
                         TaskUpload(N_("Upload done, processing, please wait..."),
                                    escape(uploaded.filename)))
    except PermissionError:
        return _err("ingest_unwritable",
                    "The ingest folder is not writable; check the /cwa-book-ingest volume", 500)
    except Exception as e:  # noqa: BLE001
        log.error_or_exception("Failed to queue format add: {}".format(e))
        return _err("server_error", "Failed to queue the format for processing", 500)

    return jsonify({"queued": uploaded.filename}), 202
