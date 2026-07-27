# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical validation and defaults for per-user web-reader appearance."""

READER_THEMES = {"lightTheme", "darkTheme", "sepiaTheme", "blackTheme"}
READER_FONTS = {"default", "Yahei", "SimSun", "KaiTi", "Arial"}
READER_SPREADS = {"spread", "nonespread"}
READER_FLOWS = {"paginated", "scrolled"}

READER_DEFAULTS = {
    "theme": "lightTheme",
    "font": "default",
    "fontSize": 100,
    "margin": 16,
    "lineHeight": 150,
    "spread": "nonespread",
    "reflow": True,
    "flow": "paginated",
    "maxColumnCount": 2,
    "maxInlineSize": 720,
    "animated": True,
    "tapToTurn": True,
}


def reader_setting_int(value, lo, hi):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(lo, min(hi, int(value)))
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return max(lo, min(hi, int(value.strip())))
    return None


def sanitize_reader_settings(payload):
    """Return only known, typed reader settings from an arbitrary mapping."""
    if not isinstance(payload, dict):
        return {}
    out = {}
    if payload.get("theme") in READER_THEMES:
        out["theme"] = payload["theme"]
    if payload.get("font") in READER_FONTS:
        out["font"] = payload["font"]
    if payload.get("spread") in READER_SPREADS:
        out["spread"] = payload["spread"]
    if payload.get("flow") in READER_FLOWS:
        out["flow"] = payload["flow"]
    for key, lo, hi in (
        ("fontSize", 75, 200),
        ("margin", 0, 80),
        ("lineHeight", 100, 220),
        ("maxColumnCount", 1, 2),
        ("maxInlineSize", 420, 1200),
    ):
        value = reader_setting_int(payload.get(key), lo, hi)
        if value is not None:
            out[key] = value
    for key in ("reflow", "animated", "tapToTurn"):
        value = payload.get(key)
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            out[key] = value.strip().lower() == "true"
    return out


def merged_reader_settings(current, patch):
    """Merge a partial client patch without erasing unrelated saved controls."""
    merged = sanitize_reader_settings(current)
    merged.update(sanitize_reader_settings(patch))
    return merged


def resolved_reader_settings(current):
    """Return the complete client contract, applying defaults to missing keys."""
    resolved = dict(READER_DEFAULTS)
    resolved.update(sanitize_reader_settings(current))
    return resolved
