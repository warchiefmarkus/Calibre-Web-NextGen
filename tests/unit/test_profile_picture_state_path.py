# SPDX-License-Identifier: GPL-3.0-or-later
"""Profile-picture state must follow CALIBRE_DBPATH instead of Docker-only /config."""
import inspect
import os

import flask
import pytest

from cps import constants
from cps import cwa_functions


@pytest.mark.unit
def test_profile_picture_store_uses_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    assert cwa_functions._user_profiles_json_path() == os.path.join(
        str(tmp_path), "user_profiles.json"
    )


@pytest.mark.unit
def test_missing_profile_picture_store_is_empty_json_not_500(monkeypatch, tmp_path):
    missing = tmp_path / "user_profiles.json"
    monkeypatch.setattr(cwa_functions, "_user_profiles_json_path", lambda: str(missing))
    app = flask.Flask(__name__)
    with app.test_request_context("/user_profiles.json"):
        response = inspect.unwrap(cwa_functions.user_profiles_json)()
    assert response.status_code == 200
    assert response.get_json() == {}


@pytest.mark.unit
def test_auth_profile_path_is_not_hardcoded_to_docker_config(monkeypatch, tmp_path):
    from cps.api import auth

    expected = str(tmp_path / "user_profiles.json")
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", expected)
    assert expected in inspect.getsource(auth._user_avatar) or "constants.USER_PROFILES_JSON" in inspect.getsource(auth._user_avatar)
