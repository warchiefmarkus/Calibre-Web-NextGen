# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calibre-native annotation adapter for the managed-library profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import json
import math
import uuid

from .calibremcp_client import (
    create_reader_annotation,
    delete_reader_annotation,
    get_reader_annotations,
    update_reader_annotation,
)

SUPPORTED_COLORS = {"yellow", "red", "green", "blue"}


def normalize_pdf_locator(payload: dict[str, Any]) -> tuple[int, str]:
    """Validate a PDF locator (legacy normalized rectangles or EmbedPDF envelope)."""
    try:
        page = int(payload.get("pdf_page") or payload.get("page"))
    except (TypeError, ValueError) as exc:
        raise ValueError("pdf_page must be a 1-based page number") from exc
    raw = payload.get("pdf_quad")
    if raw is None:
        raw = payload.get("pdf_quad_json")
    if raw is None:
        raw = payload.get("quads")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("pdf_quad must be valid JSON") from exc
    if page < 1 or not isinstance(raw, (list, dict)) or not raw:
        raise ValueError("PDF annotation requires pdf_page and pdf_quad")
    if isinstance(raw, dict):
        annotation = raw.get("annotation")
        if not isinstance(annotation, dict) or not str(annotation.get("id") or "").strip():
            raise ValueError("EmbedPDF locator requires an annotation object with id")
        page_index = annotation.get("pageIndex")
        if page_index is not None:
            try:
                normalized_page_index = int(page_index)
            except (TypeError, ValueError) as exc:
                raise ValueError("EmbedPDF annotation pageIndex must be an integer") from exc
            if normalized_page_index != page - 1:
                raise ValueError("EmbedPDF annotation page does not match pdf_page")
        requested_id = str(payload.get("annotation_id") or payload.get("uuid") or "").strip()
        if requested_id and requested_id != str(annotation.get("id")):
            raise ValueError("EmbedPDF annotation id does not match annotation_id")
        normalized = raw
    else:
        normalized = []
        for rect in raw:
            if not isinstance(rect, (list, tuple)) or len(rect) != 4:
                raise ValueError("each pdf_quad rectangle must contain x, y, width, height")
            try:
                values = [float(value) for value in rect]
            except (TypeError, ValueError) as exc:
                raise ValueError("pdf_quad values must be numbers") from exc
            x, y, width, height = values
            if not all(math.isfinite(value) for value in values):
                raise ValueError("pdf_quad values must be finite")
            if (
                x < 0 or y < 0 or width <= 0 or height <= 0
                or x + width > 1.000001 or y + height > 1.000001
            ):
                raise ValueError("pdf_quad rectangles must be normalized to the page")
            normalized.append([round(value, 8) for value in values])
    return page, json.dumps(normalized, separators=(",", ":"), sort_keys=True)


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
        position_type=item.get("position_type") or item.get("pos_type") or ("pdf_quad" if item.get("pdf_page") or item.get("page") else "cfi"),
        pdf_page=item.get("pdf_page") or item.get("page"),
        pdf_quad_json=(
            item.get("pdf_quad_json")
            if isinstance(item.get("pdf_quad_json"), str)
            else json.dumps(item.get("pdf_quad_json"), separators=(",", ":"), sort_keys=True)
            if item.get("pdf_quad_json") is not None
            else json.dumps(item.get("quads"), separators=(",", ":"), sort_keys=True)
            if item.get("quads") is not None
            else None
        ),
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
    normalized_format = fmt.upper()
    color = str(payload.get("highlight_color") or "yellow").strip().lower()
    if color not in SUPPORTED_COLORS:
        color = "yellow"
    annotation_id = str(payload.get("annotation_id") or "cwn-web-" + uuid.uuid4().hex)
    native_payload: dict[str, Any] = {
        "format": normalized_format,
        "type": "highlight",
        "uuid": annotation_id,
        "highlighted_text": payload.get("highlighted_text"),
        "notes": payload.get("note_text"),
        "style": {"kind": "color", "which": color},
        "source": "cwng-web",
    }
    if normalized_format == "PDF":
        page, quad_json = normalize_pdf_locator(payload)
        locator = json.loads(quad_json)
        native_payload.update({
            "pos_type": "pdf_quad",
            "position_type": "pdf_quad",
            "page": page,
            "pdf_page": page,
            "quads": locator,
            "pdf_quad_json": quad_json,
        })
    else:
        cfi_range = str(payload.get("cfi_range") or "").strip()
        native_cfi = epubcfi_to_native(cfi_range)
        spine_index = payload.get("spine_index", native_cfi["spine_index"])
        try:
            spine_index = max(0, int(spine_index))
        except (TypeError, ValueError):
            spine_index = native_cfi["spine_index"]
        native_payload.update({
            "start_cfi": native_cfi["start_cfi"],
            "end_cfi": native_cfi["end_cfi"],
            "spine_index": spine_index,
            "spine_name": str(payload.get("spine_name") or ""),
            "cfi_range": cfi_range,
        })
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
    if "highlighted_text" in payload:
        changes["highlighted_text"] = payload.get("highlighted_text")
    if fmt.upper() == "PDF" and any(key in payload for key in ("pdf_page", "page", "pdf_quad", "pdf_quad_json", "quads")):
        page, quad_json = normalize_pdf_locator(payload)
        locator = json.loads(quad_json)
        changes.update({
            "pos_type": "pdf_quad",
            "position_type": "pdf_quad",
            "page": page,
            "pdf_page": page,
            "quads": locator,
            "pdf_quad_json": quad_json,
        })
    updated = update_reader_annotation(cwng_user, book_id, annotation_id, changes)
    return native_to_row(updated, book_id)


def delete_annotation(
    cwng_user: str,
    book_id: int,
    annotation_id: str,
    fmt: str = "EPUB",
) -> dict[str, Any]:
    return delete_reader_annotation(cwng_user, book_id, annotation_id, fmt)
