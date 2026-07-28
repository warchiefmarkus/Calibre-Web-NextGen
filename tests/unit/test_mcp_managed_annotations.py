# SPDX-License-Identifier: GPL-3.0-or-later
"""Managed-profile native annotation adapter tests."""

from __future__ import annotations

import pytest

from cps.services.calibre_annotations import epubcfi_to_native


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
def test_reader_annotation_format_rejects_unknown_namespace():
    from flask import Flask
    from cps import annotations
    app = Flask(__name__)
    with app.test_request_context('/annotations/1/data.json?format=pdf'):
        with pytest.raises(ValueError):
            annotations._reader_annotation_format()
