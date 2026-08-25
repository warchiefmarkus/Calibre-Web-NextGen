# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sys
import os
import mimetypes

from flask import Flask, g, session
from .MyLoginManager import MyLoginManager
from flask_principal import Principal
from werkzeug.middleware.proxy_fix import ProxyFix

from . import logger
from . import constants
from . import deployment_profile
from .cli import CliParameter
from .reverseproxy import ReverseProxied
from .server import WebServer
from .updater import Updater
from . import config_sql
from . import cache_buster
from . import ub, db, magic_shelf

try:
    from flask_limiter import Limiter
    limiter_present = True
except ImportError:
    limiter_present = False
try:
    from flask_wtf.csrf import CSRFProtect
    wtf_present = True
except ImportError:
    wtf_present = False


mimetypes.init()
mimetypes.add_type('application/xhtml+xml', '.xhtml')
mimetypes.add_type('application/epub+zip', '.epub')
mimetypes.add_type('application/epub+zip', '.kepub')
mimetypes.add_type('text/xml', '.fb2')
mimetypes.add_type('application/x-mobipocket-ebook', '.mobi')
mimetypes.add_type('application/x-mobipocket-ebook', '.prc')
mimetypes.add_type('application/vnd.amazon.ebook', '.azw')
mimetypes.add_type('application/x-mobi8-ebook', '.azw3')
mimetypes.add_type('application/vnd.comicbook-rar', '.cbr')
mimetypes.add_type('application/vnd.comicbook+zip', '.cbz')
mimetypes.add_type('application/x-cbt', '.cbt')
mimetypes.add_type('application/x-7z-compressed', '.cb7')
mimetypes.add_type('image/vnd.djv', '.djv')
mimetypes.add_type('image/vnd.djv', '.djvu')
mimetypes.add_type('application/mpeg', '.mpeg')
mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('audio/x-m4a', '.m4a')
mimetypes.add_type('audio/x-m4a', '.m4b')
mimetypes.add_type('audio/x-hx-aac-adts', '.aac')
mimetypes.add_type('audio/vnd.dolby.dd-raw', '.ac3')
mimetypes.add_type('video/x-ms-asf', '.asf')
mimetypes.add_type('audio/ogg', '.ogg')
mimetypes.add_type('application/ogg', '.oga')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/x-ms-reader', '.lit')
mimetypes.add_type('text/javascript; charset=UTF-8', '.js')
mimetypes.add_type('application/vnd.adobe.adept+xml', '.acsm')
mimetypes.add_type('application/vnd.readium.lcp.license.v1.0+json', '.lcpl')
mimetypes.add_type('application/vnd.amazon.ebook', '.kfx')
mimetypes.add_type('application/zip', '.kfx-zip')

log = logger.create()

app = Flask(__name__)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true',
    SESSION_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SAMESITE='Strict',
    WTF_CSRF_SSL_STRICT=False,
    SESSION_COOKIE_NAME=os.environ.get('COOKIE_PREFIX', "") + "session",
    REMEMBER_COOKIE_NAME=os.environ.get('COOKIE_PREFIX', "") + "remember_token"
)

# Fix for running behind reverse proxy (e.g. nginx, apache, caddy, ...)
# Without it, url_for will generate http:// urls even if https:// is used
# Set TRUSTED_PROXY_COUNT to the number of proxies in your chain (default: 1)
# For CF Tunnel + reverse proxy, use TRUSTED_PROXY_COUNT=2
num_proxies = int(os.environ.get('TRUSTED_PROXY_COUNT', '1'))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=num_proxies, x_proto=num_proxies, x_host=num_proxies, x_prefix=num_proxies)
log.info(f'ProxyFix configured to trust {num_proxies} proxy(ies) for X-Forwarded-* headers')

lm = MyLoginManager()

cli_param = CliParameter()

config = config_sql.ConfigSQL()

if wtf_present:
    csrf = CSRFProtect()
else:
    csrf = None

calibre_db = db.CalibreDB()

web_server = WebServer()

updater_thread = Updater()

if limiter_present:
    limiter = Limiter(key_func=True, headers_enabled=True, auto_check=False, swallow_errors=False)
else:
    limiter = None


def apply_https_runtime_config():
    """Refresh cookie security flags from the current saved config."""
    if config.config_login_type == constants.LOGIN_OAUTH or getattr(config, 'config_use_https', False):
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        log.info("Enforcing SESSION_COOKIE_SECURE=True (OAuth enabled or HTTPS enforced)")
    else:
        app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
        log.info(f"SESSION_COOKIE_SECURE set to {app.config['SESSION_COOKIE_SECURE']} (Standard/LDAP login)")


# Last-logged magic-shelf filter snapshot per user_id; the before_request
# hook fires on every request (incl. UI polling), so the DEBUG line is
# deduped to once per change (cf. _AUTHOR_SORT_DRIFT_WARNED).
_MAGIC_SHELF_COUNTS_LOGGED = {}

# (user_id, shelf_id) pairs already warned about an orphaned system shelf,
# so that WARNING fires once per user+shelf instead of on every request.
_ORPHANED_SYSTEM_SHELF_WARNED = set()


def _log_magic_shelf_counts(user_id, total_shelves, visible_shelves,
                            hidden_templates=(), hidden_shelves=()):
    snapshot = (total_shelves, visible_shelves,
                tuple(hidden_templates), tuple(hidden_shelves))
    if _MAGIC_SHELF_COUNTS_LOGGED.get(user_id) != snapshot:
        _MAGIC_SHELF_COUNTS_LOGGED[user_id] = snapshot
        msg = (f"Found {total_shelves} total magic shelves for user {user_id}, "
               f"{visible_shelves} visible after filtering")
        if hidden_templates:
            msg += "; hidden system templates: " + ", ".join(hidden_templates)
        if hidden_shelves:
            msg += "; hidden public shelves: " + ", ".join(hidden_shelves)
        log.debug(msg)


def create_app():
    app.config["MCP_MANAGED_LIBRARY"] = deployment_profile.is_mcp_managed_library()
    # Classic templates sometimes receive the SQL-backed `config` object, which
    # shadows Flask's default `config` Jinja global. Expose the deployment flag
    # under a dedicated name so every layout render uses the same safe value.
    app.jinja_env.globals["mcp_managed_library"] = app.config["MCP_MANAGED_LIBRARY"]
    if csrf:
        csrf.init_app(app)

    cli_param.init()

    ub.init_db(cli_param.settings_path)
    # pylint: disable=no-member
    encrypt_key, error = config_sql.get_encryption_key(os.path.dirname(cli_param.settings_path))

    config_sql.load_configuration(ub.session, encrypt_key)
    config.init_config(ub.session, encrypt_key, cli_param)

    # Intelligent Security Configuration
    # Force SESSION_COOKIE_SECURE if OAuth is enabled OR if "Use via HTTPS" is checked.
    apply_https_runtime_config()

    # Set OAuth redirect host consistency
    if hasattr(config, 'config_oauth_redirect_host') and config.config_oauth_redirect_host:
        from urllib.parse import urlparse
        parsed = urlparse(config.config_oauth_redirect_host)
        if parsed.netloc:
            app.config['FORCE_HOST_FOR_REDIRECTS'] = parsed.netloc

    if error:
        log.error(error)

    ub.password_change(cli_param.user_credentials)

    if sys.version_info < (3, 0):
        log.info(
            '*** Python2 is EOL since end of 2019, this version of Calibre-Web is no longer supporting Python2, '
            'please update your installation to Python3 ***')
        print(
            '*** Python2 is EOL since end of 2019, this version of Calibre-Web is no longer supporting Python2, '
            'please update your installation to Python3 ***')
        web_server.stop(True)
        sys.exit(5)

    lm.login_view = 'web.login'
    lm.anonymous_user = ub.Anonymous
    lm.session_protection = 'strong' if config.config_session == 1 else "basic"

    from .calibre_init import init_calibre_db_from_config
    init_calibre_db_from_config(config, cli_param.settings_path)
    calibre_db.init_db()
    # The annotation content-id backfill needs both databases: app.db owns the
    # annotation, while metadata.db is authoritative for book UUID. Running it
    # earlier would let a filename choose the book and can cross-link rows.
    ub.backfill_annotation_content_ids(
        ub.session.bind,
        lambda book_id: getattr(calibre_db.get_book(book_id), "uuid", None),
    )

    updater_thread.init_updater(config, web_server)
    # Perform dry run of updater and exit afterward
    if cli_param.dry_run:
        updater_thread.dry_run()
        sys.exit(0)
    if not deployment_profile.is_mcp_managed_library():
        updater_thread.start()
    else:
        log.info("Internal updater disabled by mcp-managed-library profile")
    app.wsgi_app = ReverseProxied(app.wsgi_app)

    if os.environ.get('FLASK_DEBUG'):
        cache_buster.init_cache_busting(app)
    log.info('Starting Calibre Web...')
    Principal(app)
    lm.init_app(app)
    app.secret_key = os.getenv('SECRET_KEY', config_sql.get_flask_session_key(ub.session))

    web_server.init_app(app, config)
    from .cw_babel import babel, get_locale
    if hasattr(babel, "localeselector"):
        babel.init_app(app)
        babel.localeselector(get_locale)
    else:
        babel.init_app(app, locale_selector=get_locale)

    # Initialize OAuth blueprints AFTER babel to ensure translations are loaded
    # Issue: OAuth blueprint generation was happening during module import (before babel init),
    # causing babel.list_translations() to return empty list and hiding language options
    if ub.oauth_support:
        try:
            from . import oauth_bb
            oauth_bb.init_oauth_blueprints()
            log.info("OAuth blueprints initialized successfully")
        except Exception as e:
            log.error("Failed to initialize OAuth blueprints: %s", e)

    from . import services

    if services.ldap:
        services.ldap.init_app(app, config)
    if services.goodreads_support:
        services.goodreads_support.connect(config.config_goodreads_api_key,
                                           config.config_use_goodreads)
    config.store_calibre_uuid(calibre_db, db.Library_Id)
    # Configure rate limiter
    # https://limits.readthedocs.io/en/stable/storage.html
    app.config.update(RATELIMIT_ENABLED=config.config_ratelimiter)
    if config.config_limiter_uri != "" and not cli_param.memory_backend:
        app.config.update(RATELIMIT_STORAGE_URI=config.config_limiter_uri)
        if config.config_limiter_options != "":
            app.config.update(RATELIMIT_STORAGE_OPTIONS=config.config_limiter_options)
    else:
        # No backend configured, so we get the in-memory one. Say so rather
        # than leaving the key unset: flask_limiter warns on every startup
        # when nothing is specified, because an implicit in-memory backend is
        # wrong for the multi-worker deployments it usually sees. This server
        # is single-process, so those counters are shared by every handler
        # that reads them and the backend is right.
        #
        # This belongs here and not on the Limiter(...) constructor. A
        # constructor storage_uri outranks app.config, so it would quietly
        # override the admin's own "Limiter Backend" setting above and drop
        # them onto memory storage with no error.
        app.config.update(RATELIMIT_STORAGE_URI="memory://")
    try:
        limiter.init_app(app)
    except Exception as e:
        log.error('Wrong Flask Limiter configuration, falling back to default: {}'.format(e))
        app.config.update(RATELIMIT_STORAGE_URI="memory://")
        limiter.init_app(app)

    # Register scheduled tasks
    @app.before_request
    def _mcp_managed_library_write_guard():
        """Fail closed for upstream routes that mutate the Calibre library."""
        if not deployment_profile.is_mcp_managed_library():
            return None
        from flask import jsonify, request

        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        # The SPA /api/v1 mutation surface now delegates to CalibreMCP. Keep
        # blocking only legacy/classic blueprints whose implementations still
        # write the library directly or start CWA background mutation jobs.
        # cover_picker is intentionally not blocked: its apply route delegates
        # managed-library cover writes to CalibreMCP; its other POST routes are
        # read-only previews/searches or update CWNG-owned app state only.
        blocked_blueprints = {"editbook", "library_refresh",
                              "convert_library", "epub_fixer", "cwa_internal", "duplicates"}
        if request.blueprint in blocked_blueprints:
            return jsonify({
                "error": {
                    "code": "library_managed_by_mcp",
                    "message": "This library is managed through CalibreMCP.",
                }
            }), 409
        return None

    # CSRFProtect is initialized earlier and also uses before_request. Move the
    # managed-library guard to index 0 so blocked mutations always fail with
    # the explicit 409 contract before CSRF/auth handlers can intercept them.
    app.before_request_funcs[None].remove(_mcp_managed_library_write_guard)
    app.before_request_funcs[None].insert(0, _mcp_managed_library_write_guard)

    # Ensure a valid calibre_db session exists before handling each request
    @app.before_request
    def _cwa_ensure_db_session():
        from flask import g, request
        from .cw_login import current_user
        from sqlalchemy import or_
        import time

        if config.config_allow_reverse_proxy_header_login:
            """
            Load user from reverse proxy authentication header if configured.
            Sets g.flask_httpauth_user early so that current_user proxy resolves correctly
            for user-specific settings like theme preferences.

            This must run before any blueprint before_request handlers that access current_user.
            """

            from . import usermanagement
            user = usermanagement.load_user_from_reverse_proxy_header(request)
            if user:
                g.flask_httpauth_user = user
            else:
                # Explicitly set to None to indicate we checked but found nothing
                g.flask_httpauth_user = None

        if current_user.is_authenticated:
            try:
                # Verify required tables exist before querying
                from sqlalchemy import inspect
                inspector = inspect(ub.session.bind)
                required_tables = ['magic_shelf', 'hidden_magic_shelf_templates']
                existing_tables = inspector.get_table_names()
                
                missing_tables = [t for t in required_tables if t not in existing_tables]
                if missing_tables:
                    log.error(f"Magic shelf tables missing from database: {missing_tables}. Run migration to create them.")
                    g.magic_shelves_access = []
                    return
                
                # Get hidden items for this user (both system templates and custom shelves)
                hidden_items = ub.session.query(
                    ub.HiddenMagicShelfTemplate.template_key,
                    ub.HiddenMagicShelfTemplate.shelf_id
                ).filter(
                    ub.HiddenMagicShelfTemplate.user_id == current_user.id
                ).all()
                
                hidden_template_keys = {item.template_key for item in hidden_items if item.template_key}
                hidden_shelf_ids = {item.shelf_id for item in hidden_items if item.shelf_id}
                
                # Get user's own shelves + public shelves (will filter hidden ones below)
                g.magic_shelves_access = ub.session.query(ub.MagicShelf).filter(
                    or_(
                        ub.MagicShelf.is_public == 1,
                        ub.MagicShelf.user_id == current_user.id
                    )
                ).all()

                total_shelves = len(g.magic_shelves_access)

                # Filter out hidden items
                from . import magic_shelf
                filtered_shelves = []
                hidden_template_hits = []
                hidden_public_hits = []
                for shelf in g.magic_shelves_access:
                    # Skip hidden system templates
                    if shelf.is_system and shelf.user_id == current_user.id:
                        # Find template key for this system shelf
                        template_key = None
                        for key, template in magic_shelf.SYSTEM_SHELF_TEMPLATES.items():
                            if template['name'] == shelf.name:
                                template_key = key
                                break

                        # If template_key not found, this is an orphaned/deprecated system shelf
                        if template_key is None:
                            if (current_user.id, shelf.id) not in _ORPHANED_SYSTEM_SHELF_WARNED:
                                _ORPHANED_SYSTEM_SHELF_WARNED.add((current_user.id, shelf.id))
                                log.warning(f"System shelf '{shelf.name}' (ID: {shelf.id}) doesn't match any current template - may need migration")
                            # Show it anyway - migration should clean it up on next restart
                            filtered_shelves.append(shelf)
                            continue

                        # Skip if hidden
                        if template_key in hidden_template_keys:
                            hidden_template_hits.append(template_key)
                            continue

                    # Skip hidden custom public shelves (not owned by user)
                    if shelf.is_public == 1 and shelf.user_id != current_user.id:
                        if shelf.id in hidden_shelf_ids:
                            hidden_public_hits.append(f"'{shelf.name}' (ID: {shelf.id})")
                            continue

                    filtered_shelves.append(shelf)

                g.magic_shelves_access = filtered_shelves

                # Deduplicated — this hook fires on every request
                _log_magic_shelf_counts(current_user.id, total_shelves, len(filtered_shelves),
                                        hidden_template_hits, hidden_public_hits)

                # Magic Shelf Count Caching
                if 'magic_shelf_counts' not in session:
                    session['magic_shelf_counts'] = {}
                
                counts = session['magic_shelf_counts']
                cache_updated = False
                now = time.time()
                CACHE_DURATION = 300  # 5 minutes
                
                for shelf in g.magic_shelves_access:
                    shelf_id_str = str(shelf.id)
                    cached_data = counts.get(shelf_id_str)
                    
                    if cached_data and (now - cached_data.get('timestamp', 0) < CACHE_DURATION):
                        shelf.book_count = cached_data['count']
                    else:
                        count = magic_shelf.get_book_count_for_magic_shelf(shelf.id)
                        counts[shelf_id_str] = {'count': count, 'timestamp': now}
                        shelf.book_count = count
                        cache_updated = True
                
                if cache_updated:
                    session.modified = True

                try:
                    magic_shelf.sort_magic_shelves_for_user(g.magic_shelves_access, current_user)
                except Exception as e:
                    log.warning(f"Failed to sort magic shelves for user {current_user.id}: {e}")
            except Exception as e:
                log.error(f"Error populating magic shelves for user {current_user.id}: {str(e)}", exc_info=True)
                g.magic_shelves_access = []
        else:
            g.magic_shelves_access = []
        try:
            calibre_db.ensure_session()
        except Exception:
            # Failsafe: let route-level code handle specific DB errors
            pass

    @app.before_request
    def _clear_pending_app_password():
        """Drop a just-created app-password cleartext from session when
        the user navigates away from the profile page. Fork issue #223:
        the cleartext shows inline on /me and survives reloads of /me,
        but disappears as soon as the user clicks anything else.

        Scoped to **top-level HTML navigations** via the ``Sec-Fetch-Dest``
        header (browsers send ``document`` for address-bar navigations
        and link clicks; ``image``/``style``/``script``/``empty`` for
        sub-resources). XHR + fetch requests count as sub-resources and
        do not clear the session — only an actual page change does.
        """
        from flask import session, request
        if "pending_app_password" not in session:
            return
        # Modern browsers send Sec-Fetch-Dest on every request. Treat
        # missing header as "unknown — be conservative, don't clear"
        # so curl / older clients can't accidentally pop the cleartext.
        dest = request.headers.get("Sec-Fetch-Dest", "")
        if dest and dest != "document":
            return
        if not dest:
            # Fallback for clients without the header: only clear on
            # text/html GETs that aren't static-file fetches.
            if request.endpoint == "static" or request.method != "GET":
                return
            if not request.accept_mimetypes.accept_html:
                return
        # On a real top-level navigation, only the profile-section
        # endpoints keep the cleartext alive.
        keep_endpoints = {
            "web.profile",
            "web.app_password_create",
            "web.app_password_revoke",
        }
        if request.endpoint not in keep_endpoints:
            session.pop("pending_app_password", None)

    @app.before_request
    def _desktop_compat_fresh_snapshot():
        from flask import request
        # Rollback ends the SERIALIZABLE snapshot so the next query sees Calibre desktop's writes.
        if not calibre_db._desktop_compat or request.endpoint == 'static':
            return
        if calibre_db.session is not None:
            try:
                calibre_db.session.rollback()
                calibre_db.session.expire_all()
            except Exception as e:
                log.debug("DESKTOP_COMPAT_MODE: rollback failed, snapshot may be stale: %s", e)
        # Clear the Flask-session shelf count cache so sidebar counts stay fresh.
        session.pop('magic_shelf_counts', None)

    def _close_request_db_sessions():
        """Release only the current request greenlet's DB sessions."""
        if ub.session is not None and hasattr(ub.session, "remove"):
            try:
                ub.session.remove()
            except Exception:
                pass

        calibre_session = None
        try:
            calibre_session = calibre_db._peek_session()
        except Exception:
            pass
        if calibre_session is not None:
            try:
                calibre_session.rollback()
            except Exception:
                pass
            try:
                calibre_session.close()
            except Exception:
                pass
        if calibre_db.session_factory:
            try:
                calibre_db.session_factory.remove()
            except Exception:
                pass

    @app.teardown_request
    def shutdown_request_session(exception=None):
        # Run while the request greenlet is unquestionably still current. This
        # is what makes greenlet-scoped registries deterministic under gevent.
        _close_request_db_sessions()

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        # Safety net for non-request app contexts (scheduler/startup helpers).
        # _peek_session() above deliberately avoids creating a fresh Session
        # merely because teardown is running.
        _close_request_db_sessions()

    if deployment_profile.enable_library_automation():
        from .schedule import register_scheduled_tasks, register_startup_tasks
        register_scheduled_tasks(config.schedule_reconnect)
        register_startup_tasks()
    else:
        log.info("Library scheduler and startup automation disabled by deployment profile")

    # Moon+ synchronization is independent of library-ingest automation.
    # It polls any WebDAV implementation and serializes actual reconciliation
    # through the normal Calibre worker queue.
    from .tasks.moonreader_sync import start_moonreader_polling
    start_moonreader_polling(app)

    @app.get("/healthz")
    def _healthz():
        return {"status": "ok", "profile": deployment_profile.profile_name()}

    # Startup helpers (notably store_calibre_uuid) can materialise the root
    # greenlet's Calibre Session after setup_db has released its own connection.
    # DESKTOP_COMPAT_MODE must enter the server loop with no persistent
    # metadata.db connection; otherwise SQLite's Unix VFS retains descriptors
    # from subsequently closed request connections for POSIX-lock safety.
    if calibre_db._desktop_compat and calibre_db.session_factory is not None:
        try:
            calibre_db.session_factory.remove()
        except Exception:
            pass

    return app
