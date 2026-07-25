# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calibre-native annotation adapter for the managed-library profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import uuid

from .calibremcp_client import (
    create_reader_annotation,
    delete_reader_annotation,
    get_reader_annotations,
    update_reader_annotation,
)

SUPPORTED_COLORS = {"yellow", "red", "green", "blue"}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _split_cfi_range(value: str) -> list[str]:
    """Split a CFI range on unescaped commas outside assertion brackets."""
    parts: list[str] = []
    current: list[str] = []
    bracket_depth = 0
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "^":
            current.append(char)
            escaped = True
            continue
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        if char == "," and bracket_depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _derive_spine_index(package_path: str) -> int:
    steps = [part for part in package_path.split("/") if part]
    if len(steps) < 2:
        return 0
    numeric = steps[1].split("[", 1)[0].split(":", 1)[0]
    try:
        return max(0, int(numeric) // 2 - 1)
    except ValueError:
        return 0


def epubcfi_to_native(cfi_range: str) -> dict[str, Any]:
    """Convert an epub.js package CFI/range to Calibre content-local CFIs."""
    value = (cfi_range or "").strip()
    if not value.startswith("epubcfi(") or not value.endswith(")"):
        raise ValueError("cfi_range must be a complete epubcfi(...) value")
    inner = value[len("epubcfi("):-1]
    if "!" not in inner:
        raise ValueError("cfi_range is missing the package/content separator")
    package_path, content = inner.split("!", 1)
    parts = _split_cfi_range(content)
    if len(parts) == 1:
        start = end = "!" + parts[0]
    elif len(parts) == 2:
        start, end = ("!" + part for part in parts)
    elif len(parts) == 3:
        common, start_tail, end_tail = parts
        start = "!" + common + start_tail
        end = "!" + common + end_tail
    else:
        raise ValueError("cfi_range has an unsupported range shape")
    return {
        "start_cfi": start,
        "end_cfi": end,
        "spine_index": _derive_spine_index(package_path),
    }


@dataclass(slots=True)
class NativeAnnotationRow:
    annotation_id: str
    book_id: int
    highlighted_text: str | None = None
    highlight_color: str = "yellow"
    note_text: str | None = None
    content_id: str | None = None
    chapter_progress: float | None = None
    context_string: str | None = None
    cfi_range: str | None = None
    source: str = "calibre-native"
    created_at: datetime | None = None
    last_synced: datetime | None = None
    position_type: str | None = "cfi"
    start_container_path: str | None = None
    start_container_child_index: int | None = None
    start_offset: int | None = None
    end_container_path: str | None = None
    end_container_child_index: int | None = None
    end_offset: int | None = None
    pdf_page: int | None = None
    pdf_quad_json: str | None = None
    comic_page: int | None = None
    hidden: bool = False


def native_to_row(item: dict[str, Any], book_id: int) -> NativeAnnotationRow:
    style = item.get("style") if isinstance(item.get("style"), dict) else {}
    color = str(style.get("which") or item.get("highlight_color") or "yellow").lower()
    if color not in SUPPORTED_COLORS:
        color = "yellow"
    created = _parse_datetime(item.get("timestamp"))
    return NativeAnnotationRow(
        annotation_id=str(item.get("uuid") or item.get("annotation_id") or ""),
        book_id=book_id,
        highlighted_text=item.get("highlighted_text"),
        highlight_color=color,
        note_text=item.get("notes") if "notes" in item else item.get("note_text"),
        content_id=item.get("content_id"),
        chapter_progress=item.get("chapter_progress"),
        context_string=item.get("context_string"),
        cfi_range=item.get("cfi_range"),
        source=str(item.get("source") or "calibre-native"),
        created_at=created,
        last_synced=created,
    )


def list_annotations(cwng_user: str, book_id: int, fmt: str = "EPUB") -> list[NativeAnnotationRow]:
    payload = get_reader_annotations(cwng_user, book_id, fmt)
    annotations_map = payload.get("annotations_map", {}) if isinstance(payload, dict) else {}
    highlights = annotations_map.get("highlight", []) if isinstance(annotations_map, dict) else []
    rows = [
        native_to_row(item, book_id)
        for item in highlights
        if isinstance(item, dict) and not item.get("removed")
    ]
    rows.sort(key=lambda row: (
        row.chapter_progress is None,
        row.chapter_progress or 0,
        row.created_at.timestamp() if row.created_at else 0,
        row.annotation_id,
    ))
    return rows


def create_annotation(
    cwng_user: str,
    book_id: int,
    payload: dict[str, Any],
    fmt: str = "EPUB",
) -> NativeAnnotationRow:
    cfi_range = str(payload.get("cfi_range") or "").strip()
    native_cfi = epubcfi_to_native(cfi_range)
    color = str(payload.get("highlight_color") or "yellow").strip().lower()
    if color not in SUPPORTED_COLORS:
        color = "yellow"
    annotation_id = str(payload.get("annotation_id") or "cwn-web-" + uuid.uuid4().hex)
    spine_index = payload.get("spine_index", native_cfi["spine_index"])
    try:
        spine_index = max(0, int(spine_index))
    except (TypeError, ValueError):
        spine_index = native_cfi["spine_index"]
    native_payload: dict[str, Any] = {
        "format": fmt.upper(),
        "type": "highlight",
        "uuid": annotation_id,
        "start_cfi": native_cfi["start_cfi"],
        "end_cfi": native_cfi["end_cfi"],
        "spine_index": spine_index,
        "spine_name": str(payload.get("spine_name") or ""),
        "cfi_range": cfi_range,
        "highlighted_text": payload.get("highlighted_text"),
        "notes": payload.get("note_text"),
        "style": {"kind": "color", "which": color},
        "source": "cwng-web",
    }
    for key in ("chapter_progress", "context_string", "content_id"):
        if payload.get(key) is not None:
            native_payload[key] = payload.get(key)
    created = create_reader_annotation(cwng_user, book_id, native_payload)
    return native_to_row(created, book_id)


def update_annotation(
    cwng_user: str,
    book_id: int,
    annotation_id: str,
    payload: dict[str, Any],
    fmt: str = "EPUB",
) -> NativeAnnotationRow:
    changes: dict[str, Any] = {"format": fmt.upper()}
    if "highlight_color" in payload:
        color = str(payload.get("highlight_color") or "").strip().lower()
        if color not in SUPPORTED_COLORS:
            raise ValueError(f"unsupported highlight color {color!r}")
        changes["style"] = {"kind": "color", "which": color}
    if "note_text" in payload:
        changes["notes"] = payload.get("note_text")
    updated = update_reader_annotation(cwng_user, book_id, annotation_id, changes)
    return native_to_row(updated, book_id)


def delete_annotation(
    cwng_user: str,
    book_id: int,
    annotation_id: str,
    fmt: str = "EPUB",
) -> dict[str, Any]:
    return delete_reader_annotation(cwng_user, book_id, annotation_id, fmt)
