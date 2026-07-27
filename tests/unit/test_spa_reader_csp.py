# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-pin: the SPA reader endpoint gets the same blob: CSP allowance the
legacy reader has.

foliate-js renders in-book images and CSS as blob: URLs inside an iframe. The app's
default CSP (`img-src 'self' data:`, no blob:) blocks them, so the legacy
web.read_book endpoint relaxes img-src/style-src-elem to allow blob:. The SPA
reader is served by spa.spa_shell (one shell for all /app routes), so that
endpoint must get the same allowance or in-book images/CSS break.
"""
import inspect
from pathlib import Path
import pytest

WEB_PY = (Path(__file__).resolve().parents[2] / "cps" / "web.py").read_text()


@pytest.mark.unit
def test_reader_csp_covers_spa_endpoint():
    # The blob: allowance must be keyed on a set that includes spa.spa_shell,
    # not on web.read_book alone.
    assert 'reader_like = request.endpoint in ("web.read_book", "spa.spa_shell")' in WEB_PY, (
        "the reader blob: CSP allowance must include the spa.spa_shell endpoint"
    )


@pytest.mark.unit
def test_reader_csp_allows_blob_img_and_style():
    # Both the img-src and style-src-elem blob: relaxations must be gated on
    # reader_like (covering the SPA), not the bare web.read_book check.
    block = WEB_PY.split("def add_security_headers", 1)[1].split("\ndef ", 1)[0]
    assert "style-src-elem 'self' blob: 'unsafe-inline'" in block
    # the font/default blob: allowance is also extended to reader_like
    assert block.count("if reader_like:") >= 2
    assert "frame-src 'self' blob: data:" in block
    assert "worker-src 'self' blob:" in block


@pytest.mark.unit
def test_spa_shell_allows_external_cover_images():
    # The SPA edit page's metadata-search modal + cover picker render thumbnails
    # from external provider CDNs. spa.spa_shell must be in the img-src '*'
    # allowance alongside the legacy edit-book / cover-picker endpoints.
    block = WEB_PY.split("def add_security_headers", 1)[1].split("\ndef ", 1)[0]
    assert '"spa.spa_shell"' in block
    assert 'cover_picker.cover_picker_page' in block
