# SPDX-License-Identifier: GPL-3.0-or-later
"""Managed-profile metadata adapter contract tests."""

from __future__ import annotations

import pytest

from cps.api import edit


@pytest.mark.unit
def test_managed_metadata_payload_normalizes_spa_fields(monkeypatch):
    monkeypatch.setattr(edit, "get_locale", lambda: "en")
    payload, errors = edit._managed_metadata_payload({
        "title": " Test ",
        "authors": "Alice Smith & Bob Jones",
        "series": "Demo",
        "series_index": "2.5",
        "tags": "one, two",
        "publishers": "Publisher",
        "languages": "Ukrainian, English",
        "rating": 4.5,
        "comments": "<p>Hello</p>",
        "pubdate": "2024-05-06",
        "identifiers": [
            {"type": "ISBN", "val": "123"},
            {"type": "asin", "val": "XYZ"},
        ],
    })
    assert errors == {}
    assert payload == {
        "title": "Test",
        "authors": ["Alice Smith", "Bob Jones"],
        "series": "Demo",
        "series_index": 2.5,
        "tags": ["one", "two"],
        "publisher": "Publisher",
        "languages": ["ukr", "eng"],
        "rating": 4.5,
        "comments": "<p>Hello</p>",
        "pubdate": "2024-05-06",
        "identifiers": {"isbn": "123", "asin": "XYZ"},
    }


@pytest.mark.unit
def test_managed_metadata_payload_preserves_explicit_clears(monkeypatch):
    monkeypatch.setattr(edit, "get_locale", lambda: "en")
    payload, errors = edit._managed_metadata_payload({
        "tags": "",
        "series": "",
        "publishers": "",
        "languages": "",
        "comments": "",
        "pubdate": "",
        "identifiers": [],
        "rating": 0,
    })
    assert errors == {}
    assert payload["tags"] == []
    assert payload["series"] == ""
    assert payload["publisher"] == ""
    assert payload["languages"] == []
    assert payload["comments"] == ""
    assert payload["pubdate"] == ""
    assert payload["identifiers"] == {}
    assert payload["rating"] == 0


@pytest.mark.unit
def test_managed_metadata_payload_reports_field_errors(monkeypatch):
    monkeypatch.setattr(edit, "get_locale", lambda: "en")
    payload, errors = edit._managed_metadata_payload({
        "title": "",
        "authors": "",
        "series_index": "nope",
        "languages": "Not a language",
        "rating": 9,
        "pubdate": "06.05.2024",
        "identifiers": [
            {"type": "isbn", "val": "1"},
            {"type": "ISBN", "val": "2"},
        ],
    })
    assert payload == {}
    assert set(errors) == {
        "title", "authors", "series_index", "languages",
        "rating", "pubdate", "identifiers",
    }
