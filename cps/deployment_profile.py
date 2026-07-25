# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deployment feature gates for the Legion bare-metal CWNG fork."""

from __future__ import annotations

import os

PROFILE_ENV = "CWNG_PROFILE"
MCP_MANAGED_PROFILE = "mcp-managed-library"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def profile_name() -> str:
    return os.environ.get(PROFILE_ENV, "standard").strip().lower() or "standard"


def is_mcp_managed_library() -> bool:
    return profile_name() == MCP_MANAGED_PROFILE


def enable_koreader() -> bool:
    return not is_mcp_managed_library() or _env_bool("CWNG_ENABLE_KOREADER", False)


def enable_kobo() -> bool:
    return not is_mcp_managed_library() and _env_bool("CWNG_ENABLE_KOBO", True)


def enable_library_automation() -> bool:
    return not is_mcp_managed_library()


def use_calibre_native_reader_data() -> bool:
    """Store positions/annotations through CalibreMCP in managed deployments.

    The explicit environment switch is an emergency rollback boundary: setting
    ``CWNG_NATIVE_READER_DATA=false`` restores the untouched legacy app.db
    bookmark/annotation code without disabling the library write protections.
    """
    return is_mcp_managed_library() and _env_bool("CWNG_NATIVE_READER_DATA", True)


def enable_rag_ui() -> bool:
    """Expose the private CalibreMCP RAG adapter through authenticated CWNG UI."""
    return (
        is_mcp_managed_library()
        and _env_bool("CWNG_RAG_UI_ENABLED", True)
        and bool(os.environ.get("CWNG_CALIBREMCP_REST_TOKEN", "").strip())
    )
