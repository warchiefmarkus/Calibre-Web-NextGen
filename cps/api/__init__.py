# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned JSON API for the NextGen SPA frontend. See notes/FRONTEND-REBUILD-DESIGN.md."""
import traceback

from flask import Blueprint, jsonify, request, g
from werkzeug.exceptions import HTTPException

from .. import logger, config
from ..cw_login import current_user
from ..usermanagement import load_user_from_reverse_proxy_header

log = logger.create()

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Endpoints reachable without an authenticated session. Everything else under
# /api/v1 requires auth (or anonymous-browse mode). auth_me/auth_logout handle
# the unauthenticated case gracefully themselves, so they're allowed through too.
_PUBLIC_ENDPOINTS = {
    "api_v1.health",
    "api_v1.auth_csrf",
    "api_v1.auth_login",
    "api_v1.auth_me",
    "api_v1.auth_logout",
    "api_v1.auth_config",
    "api_v1.auth_register",
    "api_v1.auth_forgot",
    # Magic-link (remote) login is for the logged-out device by definition — the
    # endpoints self-gate on config_remote_login and consume a one-time token.
    "api_v1.auth_magic_link_start",
    "api_v1.auth_magic_link_poll",
    "api_v1.i18n_catalog",
}


@api_v1.before_request
def _require_api_auth():
    """Gate the whole API surface, returning JSON 401 (never an HTML 302) when
    unauthenticated. Mirrors usermanagement.login_required_if_no_ano so behaviour
    matches the rest of the app (reverse-proxy header login -> anon-browse ->
    session), but an SPA fetch gets a clean 401 it can act on instead of a redirect
    to the HTML login page (which would surface as a JSON parse error on session
    expiry). The per-route @login_required_if_no_ano decorators remain as
    defence-in-depth and per-route documentation."""
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if config.config_allow_reverse_proxy_header_login:
        user = load_user_from_reverse_proxy_header(request)
        if user:
            g.flask_httpauth_user = user
            return None
        g.flask_httpauth_user = None
    if config.config_anonbrowse == 1:
        return None
    if current_user.is_authenticated:
        return None
    return jsonify({"error": {"code": "unauthorized",
                              "message": "Authentication required"}}), 401


@api_v1.errorhandler(HTTPException)
def handle_http_exception(exc):
    """Return JSON instead of HTML for all HTTPExceptions raised inside the API blueprint."""
    return jsonify({"error": {"code": exc.name.lower().replace(" ", "_"),
                              "message": exc.description}}), exc.code


@api_v1.errorhandler(Exception)
def handle_generic_exception(exc):
    """Return a JSON 500 and log the full traceback; never silently swallow."""
    log.error("Unhandled exception in api_v1: %s", traceback.format_exc())
    return jsonify({"error": {"code": "internal_server_error",
                              "message": "An unexpected error occurred"}}), 500


@api_v1.route("/health")
def health():
    return jsonify({"status": "ok", "api": "v1"})


# Route modules attach their views to api_v1 on import; import LAST so api_v1 exists.
from . import auth     # noqa: E402,F401
from . import i18n     # noqa: E402,F401
from . import books    # noqa: E402,F401
from . import external_ratings  # noqa: E402,F401
from . import actions  # noqa: E402,F401
from . import browse   # noqa: E402,F401
from . import shelves  # noqa: E402,F401
from . import search   # noqa: E402,F401
from . import account  # noqa: E402,F401
from . import reader   # noqa: E402,F401
from . import reader_translation  # noqa: E402,F401
from . import rag      # noqa: E402,F401
from . import edit     # noqa: E402,F401
from . import upload   # noqa: E402,F401
from . import admin    # noqa: E402,F401
from . import info     # noqa: E402,F401
from . import duplicates  # noqa: E402,F401
from . import magicshelves  # noqa: E402,F401
from . import comic     # noqa: E402,F401
from . import admin_security  # noqa: E402,F401
