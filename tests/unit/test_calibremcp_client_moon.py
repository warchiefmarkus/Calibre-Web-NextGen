# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def test_list_books_paginates_full_library():
    from cps.services import calibremcp_client as client

    responses = [
        {"items": [{"id": 1}, {"id": 2}], "total": 3},
        {"items": [{"id": 3}], "total": 3},
    ]
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs["params"]["offset"])
        return responses.pop(0)

    with patch.object(client, "_request", side_effect=request):
        books = client.list_books("admin")

    assert [row["id"] for row in books] == [1, 2, 3]
    assert calls == [0, 2]
