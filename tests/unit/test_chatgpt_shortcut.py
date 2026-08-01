# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
BOOK_DETAIL = (ROOT / "frontend/src/pages/BookDetail.tsx").read_text(encoding="utf-8")
PACKAGE = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))


def test_chatgpt_shortcut_uses_fontawesome_openai_brand_icon():
    assert "import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';" in BOOK_DETAIL
    assert "import { faOpenai } from '@fortawesome/free-brands-svg-icons';" in BOOK_DETAIL
    assert "<FontAwesomeIcon icon={faOpenai}" in BOOK_DETAIL
    assert "data-testid=\"chatgpt-similar-books\"" in BOOK_DETAIL
    assert "<ExternalLink" not in BOOK_DETAIL


def test_fontawesome_runtime_dependencies_are_declared():
    dependencies = PACKAGE["dependencies"]
    assert "@fortawesome/react-fontawesome" in dependencies
    assert "@fortawesome/free-brands-svg-icons" in dependencies
