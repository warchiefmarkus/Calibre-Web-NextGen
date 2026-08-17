# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate and stage cover images for the managed-library adapter."""

from __future__ import annotations

import io
import os
from pathlib import Path
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
import requests

from .. import cw_advocate
from . import cover_url_validator


class ManagedCoverError(ValueError):
    pass


_STAGING_ROOT = Path(os.environ.get(
    "CWNG_COVER_STAGING_DIR",
    "/root/calibre/CalibreWeb/var/staging/covers",
))
_MAX_BYTES = int(os.environ.get(
    "CWA_COVER_DOWNLOAD_MAX_BYTES", str(15 * 1024 * 1024)
))
_MAX_PIXELS = int(os.environ.get("CWNG_COVER_MAX_PIXELS", "60000000"))


def _read_limited(stream) -> bytes:
    content = bytearray()
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > _MAX_BYTES:
            raise ManagedCoverError(
                f"Cover image exceeds the {_MAX_BYTES // (1024 * 1024)} MB limit."
            )
    if not content:
        raise ManagedCoverError("Cover image is empty.")
    return bytes(content)


def _staging_path() -> Path:
    _STAGING_ROOT.mkdir(parents=True, exist_ok=True, mode=0o2750)
    try:
        _STAGING_ROOT.chmod(0o2750)
    except OSError:
        pass
    return _STAGING_ROOT / f"cover-{uuid.uuid4().hex}.jpg"


def _normalize_to_jpeg(content: bytes) -> Path:
    target = _staging_path()
    try:
        with Image.open(io.BytesIO(content)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise ManagedCoverError("Cover image has invalid dimensions.")
            if image.width * image.height > _MAX_PIXELS:
                raise ManagedCoverError("Cover image dimensions are too large.")
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = image.convert("RGB")
            rgb.save(target, format="JPEG", quality=92, optimize=True)
        target.chmod(0o640)
        return target
    except (UnidentifiedImageError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise ManagedCoverError("Cover-file is not a valid image.") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise


def stage_uploaded_cover(file_storage) -> Path:
    if file_storage is None:
        raise ManagedCoverError("No cover file was provided.")
    stream = getattr(file_storage, "stream", file_storage)
    return _normalize_to_jpeg(_read_limited(stream))


def stage_remote_cover(url: str) -> Path:
    validation = cover_url_validator.validate_cover_url(url)
    if not validation.valid:
        raise ManagedCoverError(
            validation.error_message or "The cover URL is not valid."
        )
    response = None
    try:
        response = cw_advocate.get(
            validation.url,
            timeout=(10, 30),
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise ManagedCoverError(
                f"Cover server returned HTTP {response.status_code}."
            )
        content = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > _MAX_BYTES:
                raise ManagedCoverError(
                    f"Cover image exceeds the {_MAX_BYTES // (1024 * 1024)} MB limit."
                )
        return _normalize_to_jpeg(bytes(content))
    except ManagedCoverError:
        raise
    except requests.RequestException as exc:
        raise ManagedCoverError("Could not download the cover image.") from exc
    finally:
        if response is not None:
            response.close()


def stage_cover(*, upload=None, url: str | None = None) -> Path:
    if upload is not None:
        return stage_uploaded_cover(upload)
    if url and url.strip():
        return stage_remote_cover(url.strip())
    raise ManagedCoverError("Provide an image file or a cover URL.")
