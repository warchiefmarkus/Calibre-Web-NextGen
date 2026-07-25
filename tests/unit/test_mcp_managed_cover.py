# SPDX-License-Identifier: GPL-3.0-or-later
"""Managed cover staging tests."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
import pytest
from werkzeug.datastructures import FileStorage

from cps.services import managed_cover


@pytest.mark.unit
def test_uploaded_png_is_normalized_to_private_jpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_cover, "_STAGING_ROOT", tmp_path)
    source = io.BytesIO()
    Image.new("RGBA", (32, 48), (255, 0, 0, 128)).save(source, format="PNG")
    source.seek(0)
    upload = FileStorage(stream=source, filename="cover.png", content_type="image/png")

    staged = managed_cover.stage_uploaded_cover(upload)
    try:
        assert staged.parent == tmp_path
        assert staged.suffix == ".jpg"
        assert staged.stat().st_mode & 0o777 == 0o640
        with Image.open(staged) as image:
            assert image.format == "JPEG"
            assert image.mode == "RGB"
            assert image.size == (32, 48)
    finally:
        staged.unlink(missing_ok=True)


@pytest.mark.unit
def test_invalid_upload_is_rejected_without_staging_file(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_cover, "_STAGING_ROOT", tmp_path)
    upload = FileStorage(
        stream=io.BytesIO(b"not-an-image"),
        filename="cover.jpg",
        content_type="image/jpeg",
    )
    with pytest.raises(managed_cover.ManagedCoverError, match="valid image"):
        managed_cover.stage_uploaded_cover(upload)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_upload_limit_is_enforced_before_decode(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_cover, "_STAGING_ROOT", tmp_path)
    monkeypatch.setattr(managed_cover, "_MAX_BYTES", 16)
    upload = FileStorage(
        stream=io.BytesIO(b"x" * 17),
        filename="cover.jpg",
        content_type="image/jpeg",
    )
    with pytest.raises(managed_cover.ManagedCoverError, match="exceeds"):
        managed_cover.stage_uploaded_cover(upload)
    assert not Path(tmp_path).joinpath("cover.jpg").exists()
