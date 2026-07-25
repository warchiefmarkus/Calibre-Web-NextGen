# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace

import pytest

from cps.api import upload


class _Session:
    def __init__(self):
        self.value = None
        self.add_count = 0
        self.commit_count = 0

    def get(self, _model, _book_id):
        return self.value

    def add(self, value):
        self.value = value
        self.add_count += 1

    def commit(self):
        self.commit_count += 1


@pytest.mark.unit
def test_original_filename_record_is_idempotent(monkeypatch):
    session = _Session()
    monkeypatch.setattr(upload.ub, "session", session)

    assert upload._record_original_filename(42, "book.epub") == "created"
    assert upload._record_original_filename(42, "book.epub") == "unchanged"
    assert session.add_count == 1
    assert session.commit_count == 1

    session.value = SimpleNamespace(filename="first.epub")
    assert upload._record_original_filename(42, "second.epub") == "kept_existing"
    assert session.add_count == 1
    assert session.commit_count == 1
