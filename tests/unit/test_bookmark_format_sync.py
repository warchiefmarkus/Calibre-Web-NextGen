"""Regression coverage for #805's shared classic/SPA EPUB bookmark row."""
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest


def _classic_save(fmt="EPUB", cfi="epubcfi(/6/2)"):
    from cps import web

    session = MagicMock()
    app = flask.Flask(__name__)
    with app.test_request_context(f"/ajax/bookmark/7/{fmt}", method="POST", data={"bookmark": cfi}), \
         patch.object(web, "current_user", SimpleNamespace(id=3)), \
         patch.object(web.ub, "session", session), \
         patch.object(web.ub, "session_commit"):
        response = inspect.unwrap(web.set_bookmark)(7, fmt)
    return response, session


@pytest.mark.unit
def test_set_bookmark_stores_lowercase():
    """A legacy uppercase URL must create the canonical lowercase row."""
    response, session = _classic_save()
    assert response[1] == 201
    row = session.merge.call_args.args[0]
    assert row.format == "epub"


@pytest.mark.unit
def test_spa_and_classic_share_row():
    """Both write paths use the identical canonical EPUB format value."""
    from cps.api import reader
    from cps import web

    spa_session = MagicMock()
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books/7/bookmark", method="POST",
                                  json={"format": "epub", "bookmark": "cfiA"}), \
         patch.object(reader, "current_user", SimpleNamespace(id=3, is_authenticated=True, is_anonymous=False)), \
         patch.object(reader.ub, "session", spa_session), \
         patch.object(reader.ub, "session_commit"), \
         patch.object(reader.calibre_db, "get_filtered_book", return_value=SimpleNamespace(id=7)):
        assert inspect.unwrap(reader.save_bookmark)(7)[1] == 204
    spa_row = spa_session.merge.call_args.args[0]
    assert spa_row.format == "epub"

    _, classic_session = _classic_save("EPUB", "cfiB")
    classic_row = classic_session.merge.call_args.args[0]
    assert classic_row.format == spa_row.format == "epub"
    assert {spa_row.format, classic_row.format} == {"epub"}

    read_source = inspect.getsource(web.read_book)
    assert "ub.Bookmark.format == book_format.lower()" in read_source
    assert 'sibling = "epub" if book_format.lower() == "kepub" else "kepub"' in read_source


@pytest.mark.unit
def test_read_book_query_lowercase():
    from cps import web

    source = inspect.getsource(web.read_book)
    assert "ub.Bookmark.format == book_format.lower()" in source
    bookmark_lookup = source[source.index("bm_q = ub.session.query(ub.Bookmark)"):]
    bookmark_lookup = bookmark_lookup[:bookmark_lookup.index("kosync_progress = None")]
    assert "ub.Bookmark.format == book_format.upper()" not in bookmark_lookup


@pytest.mark.unit
def test_classic_format_normalization_source_pins():
    from cps import web

    assert "book_format = (book_format or \"\").lower()" in inspect.getsource(web.set_bookmark)
    template = (Path(__file__).parents[2] / "cps/templates/read.html").read_text()
    assert "book_format=book_format|lower" in template
    assert "csrfToken:" in template
