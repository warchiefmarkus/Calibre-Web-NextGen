# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate and stage cover images for the managed-library adapter."""

from __future__ import annotations

import io
import os
import time
from pathlib import Path
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
import requests

from .. import cw_advocate
from ..cw_advocate.exceptions import UnacceptableAddressException


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


def stage_cover_bytes(content: bytes) -> Path:
    """Normalize in-memory cover bytes into the private MCP staging area."""
    return _normalize_to_jpeg(_read_limited(io.BytesIO(content)))


def stage_uploaded_cover(file_storage) -> Path:
    if file_storage is None:
        raise ManagedCoverError("No cover file was provided.")
    stream = getattr(file_storage, "stream", file_storage)
    return _normalize_to_jpeg(_read_limited(stream))


def stage_remote_cover(url: str) -> Path:
    """Download and stage a remote cover with a guarded GET.

    Do not preflight with HEAD here. Several cover CDNs serve image GETs but
    reject or stall HEAD requests, which made provider candidates visible in
    the picker yet impossible to apply. ``cw_advocate`` validates every hop,
    so the GET retains the same SSRF boundary while matching the real save.

    CDN DNS answers can also rotate between edges. Retry transport failures
    with a fresh request so one dead edge does not immediately fail the apply.
    """
    url = (url or "").strip()
    if not url:
        raise ManagedCoverError("Provide a cover URL.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ManagedCoverError("Cover URL must start with http:// or https://.")

    last_transport_error = None
    for attempt in range(3):
        response = None
        try:
            response = cw_advocate.get(
                url, timeout=(5, 30), allow_redirects=True, stream=True
            )
            if response.status_code != 200:
                raise ManagedCoverError(
                    f"Cover server returned HTTP {response.status_code}."
                )
            size_hint = response.headers.get("Content-Length")
            if size_hint and size_hint.isdigit() and int(size_hint) > _MAX_BYTES:
                raise ManagedCoverError(
                    f"Cover image exceeds the {_MAX_BYTES // (1024 * 1024)} MB limit."
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
        except UnacceptableAddressException as exc:
            raise ManagedCoverError(
                "That cover URL points to an internal or local address."
            ) from exc
        except ManagedCoverError:
            raise
        except requests.RequestException as exc:
            last_transport_error = exc
            # Keep enough detail server-side to diagnose provider/CDN failures;
            # the UI still receives a short safe error string.
            import logging
            logging.getLogger(__name__).warning(
                "managed cover download attempt %d/3 failed for %s: %r",
                attempt + 1, url, exc,
            )
            if attempt == 2:
                break
            time.sleep(0.25 * (attempt + 1))
        finally:
            if response is not None:
                response.close()

    raise ManagedCoverError("Could not download the cover image.") from last_transport_error


def stage_cover(*, upload=None, url: str | None = None) -> Path:
    if upload is not None:
        return stage_uploaded_cover(upload)
    if url and url.strip():
        return stage_remote_cover(url.strip())
    raise ManagedCoverError("Provide an image file or a cover URL.")
