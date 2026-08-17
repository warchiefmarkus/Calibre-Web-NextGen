# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private staging for adding a format to an existing managed book."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

from .. import constants


class ManagedFormatError(ValueError):
    pass


_STAGING_ROOT = Path(os.environ.get(
    "CWNG_FORMAT_STAGING_DIR",
    "/root/calibre/CalibreWeb/var/staging/uploads",
))
_MAX_BYTES = int(os.environ.get(
    "CWNG_FORMAT_MAX_BYTES", str(500 * 1024 * 1024)
))
_ALLOWED = frozenset(item.lower() for item in constants.EXTENSIONS_UPLOAD)


def _extension(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if not suffix or suffix not in _ALLOWED:
        raise ManagedFormatError(
            "File type is not allowed for a Calibre book format."
        )
    return suffix


def stage_uploaded_format(file_storage) -> Path:
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ManagedFormatError("No format file was provided.")
    extension = _extension(file_storage.filename)
    _STAGING_ROOT.mkdir(parents=True, exist_ok=True, mode=0o2750)
    try:
        _STAGING_ROOT.chmod(0o2750)
    except OSError:
        pass

    final_path = _STAGING_ROOT / f"format-{uuid.uuid4().hex}.{extension}"
    temp_path = final_path.with_suffix(final_path.suffix + ".part")
    total = 0
    try:
        with open(temp_path, "xb") as output:
            os.chmod(temp_path, 0o640)
            stream = getattr(file_storage, "stream", file_storage)
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_BYTES:
                    raise ManagedFormatError(
                        f"Format file exceeds the {_MAX_BYTES // (1024 * 1024)} MB limit."
                    )
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total == 0:
            raise ManagedFormatError("Format file is empty.")
        os.replace(temp_path, final_path)
        final_path.chmod(0o640)
        return final_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
