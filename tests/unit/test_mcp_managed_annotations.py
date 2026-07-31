# SPDX-License-Identifier: GPL-3.0-or-later
"""Managed-profile native annotation adapter tests."""

from __future__ import annotations

import json
import pytest

from cps.services.calibre_annotations import epubcfi_to_native, normalize_pdf_locator


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cfi", "start", "end", "spine_index"),
    [
        (
            "epubcfi(/6/4!/4/2,/1:0,/1:9)",
            "!/4/2/1:0",
            "!/4/2/1:9",
            1,
        ),
        (
            "epubcfi(/6/18!/4[kobo.4.1]:0,/4[kobo.4.2]:116)",
            "!/4[kobo.4.1]:0",
            "!/4[kobo.4.2]:116",
            8,
        ),
        (
            "epubcfi(/6/2!/4/2/2[kobo.1.1],/1:0,/1:15)",
            "!/4/2/2[kobo.1.1]/1:0",
            "!/4/2/2[kobo.1.1]/1:15",
            0,
        ),
        (
            "epubcfi(/6/4!/4/2/1:3)",
            "!/4/2/1:3",
            "!/4/2/1:3",
            1,
        ),
    ],
)
def test_epubcfi_to_native(cfi, start, end, spine_index):
    result = epubcfi_to_native(cfi)
    assert result == {
        "start_cfi": start,
        "end_cfi": end,
        "spine_index": spine_index,
    }


@pytest.mark.unit
def test_epubcfi_to_native_rejects_invalid_value():
    with pytest.raises(ValueError):
        epubcfi_to_native("/6/4!/4/2/1:3")


@pytest.mark.unit
def test_reader_annotation_format_preserves_fb2_namespace():
    from flask import Flask
    from cps import annotations
    app = Flask(__name__)
    with app.test_request_context('/annotations/1/data.json?format=fb2'):
        assert annotations._reader_annotation_format() == 'FB2'
    with app.test_request_context('/annotations/1', method='POST', json={'format': 'azw3'}):
        assert annotations._reader_annotation_format({'format': 'azw3'}) == 'AZW3'


@pytest.mark.unit
def test_reader_annotation_format_accepts_pdf_namespace():
    from flask import Flask
    from cps import annotations
    app = Flask(__name__)
    with app.test_request_context('/annotations/1/data.json?format=pdf'):
        assert annotations._reader_annotation_format() == 'PDF'


@pytest.mark.unit
def test_reader_annotation_format_rejects_unknown_namespace():
    from flask import Flask
    from cps import annotations
    app = Flask(__name__)
    with app.test_request_context('/annotations/1/data.json?format=djvu'):
        with pytest.raises(ValueError):
            annotations._reader_annotation_format()


@pytest.mark.unit
def test_normalize_pdf_locator_preserves_embedpdf_transfer_item():
    locator = {
        "annotation": {
            "id": "embed-1",
            "pageIndex": 2,
            "type": 9,
            "rect": {"origin": {"x": 10, "y": 20}, "size": {"width": 30, "height": 4}},
            "contents": "note",
        },
        "ctx": {"mimeType": "image/png", "data": {"__embedpdfBinary": True, "data": "AA=="}},
    }
    page, serialized = normalize_pdf_locator({
        "pdf_page": 3,
        "pdf_quad": locator,
    })
    assert page == 3
    assert json.loads(serialized) == locator


@pytest.mark.unit
def test_normalize_pdf_locator_rejects_page_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        normalize_pdf_locator({
            "pdf_page": 3,
            "pdf_quad": {"annotation": {"id": "embed-1", "pageIndex": 3}},
        })


@pytest.mark.unit
def test_normalize_pdf_locator_rejects_uid_mismatch():
    with pytest.raises(ValueError, match="does not match annotation_id"):
        normalize_pdf_locator({
            "annotation_id": "server-id",
            "pdf_page": 1,
            "pdf_quad": {"annotation": {"id": "other-id", "pageIndex": 0}},
        })
