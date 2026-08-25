# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_native_pairs_never_opens_metadata_db(monkeypatch):
    from cps.services import calibremcp_client
    from cps.services import moonreader_webdav as mod

    monkeypatch.setattr(
        mod.deployment_profile, "use_calibre_native_reader_data", lambda: True,
    )
    monkeypatch.setattr(
        calibremcp_client, "get_reader_position_pairs",
        lambda _user: {(7, "FB2"), (8, "EPUB")},
    )
    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *_args, **_kwargs: pytest.fail("Moon sync opened metadata.db directly"),
    )

    assert mod._native_pairs("admin") == {(7, "FB2"), (8, "EPUB")}
