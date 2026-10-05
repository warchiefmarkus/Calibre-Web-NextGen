# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #572 — direct edit actions and inline book-detail tag editing.

The live Playwright test verifies that Edit in the cover action dialog opens
the editor directly. Keep the role gate here because losing it could expose an
edit affordance to viewers even when the server correctly denies the route.
"""
import pathlib

import pytest

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"

pytestmark = pytest.mark.unit


def test_catalog_wires_quick_edit_gated_on_edit_role():
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert "useMe" in src
    assert "quickEdit=" in src
    # Gated on the edit permission, and never shown while selecting.
    assert "role?.edit" in src


def test_quick_edit_does_not_squeeze_the_read_action():
    """Read and edit actions share normal flow and remain usable on narrow cards."""
    src = (_FE / "components" / "BookCard.tsx").read_text()
    css = (_FE / "components" / "BookCard.module.css").read_text()
    assert "readNowInset" not in src
    assert ".readNowInset" not in css
    assert "hasActionRow" in src
    assert "styles.actionRow" in src
    assert "styles.readNowLabel" in src
    quick_edit = css[css.index(".quickEditBtn {"):css.index(".wrap:hover .quickEditBtn")]
    assert "margin-left: auto" in quick_edit
    assert "position: absolute" not in quick_edit
    action_row = css[css.index(".actionRow {"):css.index(".readNow {")]
    assert "display: flex" in action_row
    assert "flex-wrap: wrap" in action_row
    read_now = css[css.index(".readNow {"):css.index(".readNow svg")]
    assert "white-space: nowrap" in read_now
    assert "overflow: hidden" in read_now


def test_book_detail_has_inline_tag_editor():
    src = (_FE / "pages" / "BookDetail.tsx").read_text()
    assert "useUpdateMetadata" in src
    # A dedicated inline tag editor component drives add/remove.
    assert "TagEditor" in src
    # Add path: a text input + an add action.
    assert "Add tag" in src or "addTag" in src
    # Remove path: a per-tag remove control.
    assert "removeTag" in src or "Remove tag" in src


def test_tag_editor_uses_replace_semantics_from_current_tags():
    """Add/remove must rebuild the comma-separated tags string from the book's
    current tags and POST it (the /metadata endpoint has replace semantics), not
    send a lone value that would wipe the rest."""
    src = (_FE / "pages" / "BookDetail.tsx").read_text()
    # Joins names back into the comma-separated string the endpoint expects.
    assert ".join(', ')" in src or '.join(", ")' in src
    # Submits via the tags field of the metadata update.
    assert "tags:" in src
