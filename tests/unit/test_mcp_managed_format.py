# SPDX-License-Identifier: GPL-3.0-or-later
"""Managed book-format staging tests."""

from __future__ import annotations

import io

import pytest
from werkzeug.datastructures import FileStorage

from cps.services import managed_format


@pytest.mark.unit
def test_allowed_format_is_staged_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_format, "_STAGING_ROOT", tmp_path)
    upload = FileStorage(
        stream=io.BytesIO(b"<FictionBook></FictionBook>"),
        filename="book.fb2",
        content_type="application/xml",
    )
    staged = managed_format.stage_uploaded_format(upload)
    try:
        assert staged.parent == tmp_path
        assert staged.suffix == ".fb2"
        assert staged.read_bytes() == b"<FictionBook></FictionBook>"
        assert staged.stat().st_mode & 0o777 == 0o640
        assert not list(tmp_path.glob("*.part"))
    finally:
        staged.unlink(missing_ok=True)


@pytest.mark.unit
def test_disallowed_extension_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_format, "_STAGING_ROOT", tmp_path)
    upload = FileStorage(
        stream=io.BytesIO(b"MZ"),
        filename="book.exe",
        content_type="application/octet-stream",
    )
    with pytest.raises(managed_format.ManagedFormatError, match="not allowed"):
        managed_format.stage_uploaded_format(upload)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_format_size_limit_cleans_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_format, "_STAGING_ROOT", tmp_path)
    monkeypatch.setattr(managed_format, "_MAX_BYTES", 4)
    upload = FileStorage(
        stream=io.BytesIO(b"12345"),
        filename="book.txt",
        content_type="text/plain",
    )
    with pytest.raises(managed_format.ManagedFormatError, match="exceeds"):
        managed_format.stage_uploaded_format(upload)
    assert list(tmp_path.iterdir()) == []
