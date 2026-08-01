# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical validation and defaults for per-user web-reader appearance."""

import re

READER_THEMES = {"lightTheme", "darkTheme", "sepiaTheme", "blackTheme"}
READER_FONTS = {"default", "Yahei", "SimSun", "KaiTi", "Arial"}
READER_SPREADS = {"spread", "nonespread"}
READER_FLOWS = {"paginated", "scrolled"}
READER_TRANSLATION_VIEWS = {"original", "translated"}
READER_LANGUAGE_RE = r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$"

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
    "justifyText": False,
    "translationEnabled": False,
    "translationView": "original",
    "translationSourceLanguage": "auto",
    "translationTargetLanguage": "uk",
    "translationProfileId": "",
    "translationPrompt": "",
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
    if payload.get("translationView") in READER_TRANSLATION_VIEWS:
        out["translationView"] = payload["translationView"]

    source_language = str(payload.get("translationSourceLanguage") or "").strip()
    if source_language == "auto" or re.fullmatch(READER_LANGUAGE_RE, source_language):
        out["translationSourceLanguage"] = source_language
    target_language = str(payload.get("translationTargetLanguage") or "").strip()
    if re.fullmatch(READER_LANGUAGE_RE, target_language):
        out["translationTargetLanguage"] = target_language

    if "translationProfileId" in payload:
        profile_id = str(payload.get("translationProfileId") or "").strip()
        if len(profile_id) <= 64:
            out["translationProfileId"] = profile_id
    prompt = payload.get("translationPrompt")
    if isinstance(prompt, str) and len(prompt) <= 6000:
        out["translationPrompt"] = prompt.strip()

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
    for key in ("reflow", "animated", "tapToTurn", "justifyText", "translationEnabled"):
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
