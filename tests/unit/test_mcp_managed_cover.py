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


@pytest.mark.unit
def test_in_memory_cover_is_normalized_to_private_jpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_cover, "_STAGING_ROOT", tmp_path)
    source = io.BytesIO()
    Image.new("RGB", (24, 36), "navy").save(source, format="PNG")

    staged = managed_cover.stage_cover_bytes(source.getvalue())
    try:
        assert staged.parent == tmp_path
        assert staged.suffix == ".jpg"
        with Image.open(staged) as image:
            assert image.format == "JPEG"
            assert image.size == (24, 36)
    finally:
        staged.unlink(missing_ok=True)

@pytest.mark.unit
def test_remote_cover_uses_guarded_get_without_head_preflight(tmp_path, monkeypatch):
    """Provider CDNs may reject HEAD while serving GET; apply must use real GET."""
    monkeypatch.setattr(managed_cover, "_STAGING_ROOT", tmp_path)
    source = io.BytesIO()
    Image.new("RGB", (40, 60), "green").save(source, format="JPEG")
    payload = source.getvalue()

    class Response:
        status_code = 200
        headers = {"Content-Length": str(len(payload)), "Content-Type": "image/jpeg"}
        def iter_content(self, chunk_size=8192):
            yield payload
        def close(self):
            pass

    calls = []
    monkeypatch.setattr(
        managed_cover.cw_advocate,
        "get",
        lambda url, **kwargs: calls.append((url, kwargs)) or Response(),
    )
    staged = managed_cover.stage_remote_cover("https://cdn.example/cover.jpg")
    try:
        assert calls and calls[0][0] == "https://cdn.example/cover.jpg"
        assert calls[0][1]["allow_redirects"] is True
        with Image.open(staged) as image:
            assert image.format == "JPEG"
            assert image.size == (40, 60)
    finally:
        staged.unlink(missing_ok=True)


@pytest.mark.unit
def test_remote_cover_retries_transport_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_cover, "_STAGING_ROOT", tmp_path)
    source = io.BytesIO()
    Image.new("RGB", (30, 45), "blue").save(source, format="JPEG")
    payload = source.getvalue()

    class Response:
        status_code = 200
        headers = {"Content-Length": str(len(payload))}
        def iter_content(self, chunk_size=8192):
            yield payload
        def close(self):
            pass

    calls = {"n": 0}
    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise managed_cover.requests.ConnectTimeout("dead CDN edge")
        return Response()

    monkeypatch.setattr(managed_cover.cw_advocate, "get", flaky)
    monkeypatch.setattr(managed_cover.time, "sleep", lambda _seconds: None)
    staged = managed_cover.stage_remote_cover("https://cdn.example/cover.jpg")
    try:
        assert calls["n"] == 2
        with Image.open(staged) as image:
            assert image.size == (30, 45)
    finally:
        staged.unlink(missing_ok=True)


@pytest.mark.unit
def test_remote_cover_keeps_ssrf_guard(monkeypatch):
    def blocked(*args, **kwargs):
        raise managed_cover.UnacceptableAddressException("127.0.0.1")
    monkeypatch.setattr(managed_cover.cw_advocate, "get", blocked)
    with pytest.raises(managed_cover.ManagedCoverError, match="internal or local"):
        managed_cover.stage_remote_cover("http://127.0.0.1/cover.jpg")
