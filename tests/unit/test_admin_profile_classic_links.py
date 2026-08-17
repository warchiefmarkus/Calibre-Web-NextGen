# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for optional classic-admin routes in managed profiles."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAYOUT = REPO_ROOT / "cps" / "templates" / "layout.html"
ADMIN_TEMPLATE = REPO_ROOT / "cps" / "templates" / "admin.html"
RENDER_TEMPLATE = REPO_ROOT / "cps" / "render_template.py"
APP_INIT = REPO_ROOT / "cps" / "__init__.py"
ADMIN_PY = REPO_ROOT / "cps" / "admin.py"
SPA_ADMIN = REPO_ROOT / "frontend" / "src" / "pages" / "Admin.tsx"


def test_classic_layout_uses_explicit_managed_profile_context():
    layout = LAYOUT.read_text(encoding="utf-8")
    renderer = RENDER_TEMPLATE.read_text(encoding="utf-8")
    app_init = APP_INIT.read_text(encoding="utf-8")
    assert 'config.get("MCP_MANAGED_LIBRARY"' not in layout
    assert "not mcp_managed_library" in layout
    assert "mcp_managed_library=deployment_profile.is_mcp_managed_library()" in renderer
    assert 'app.jinja_env.globals["mcp_managed_library"]' in app_init


def test_optional_classic_admin_links_follow_deployment_profile():
    template = ADMIN_TEMPLATE.read_text(encoding="utf-8")
    admin = ADMIN_PY.read_text(encoding="utf-8")
    assert "'koreader': deployment_profile.enable_koreader()" in admin
    assert "'library_automation': deployment_profile.enable_library_automation()" in admin
    assert "{% if feature_support.get('koreader') %}" in template
    assert "{% if feature_support.get('library_automation') %}" in template


def test_moon_reader_sync_is_visible_from_more_server_configuration():
    src = SPA_ADMIN.read_text(encoding="utf-8")
    assert "href: '/account/moonreader'" in src
    assert "label: 'Moon+ Reader sync'" in src
    moon_entry = next(line for line in src.splitlines() if "href: '/account/moonreader'" in line)
    assert "spa: true" in moon_entry
