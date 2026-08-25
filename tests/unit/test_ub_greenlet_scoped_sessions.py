# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""app.db sessions must be isolated between concurrent gevent requests."""

import gevent
import pytest
from sqlalchemy import create_engine

from cps.ub import _make_app_session_factory

pytestmark = pytest.mark.unit


def test_app_db_factory_gives_each_greenlet_its_own_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    factory = _make_app_session_factory(engine)
    sessions = {}

    def grab(name):
        sessions[name] = factory()
        gevent.sleep(0)

    jobs = [gevent.spawn(grab, "a"), gevent.spawn(grab, "b")]
    gevent.joinall(jobs, timeout=5)
    try:
        assert sessions["a"] is not sessions["b"]
    finally:
        for job in jobs:
            factory.registry.clear()
        engine.dispose()
