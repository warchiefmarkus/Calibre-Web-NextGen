# -*- coding: utf-8 -*-
from pathlib import Path

from babel.messages.pofile import read_po


ROOT = Path(__file__).resolve().parents[2]
UK_PO = ROOT / "cps/translations/uk/LC_MESSAGES/messages.po"


def _catalog():
    with UK_PO.open("rb") as handle:
        return read_po(handle)


def test_uk_rag_and_shared_ui_strings_are_not_cross_locale_contaminated():
    catalog = _catalog()
    expected = {
        "RAG search": "RAG-пошук",
        "Search mode": "Режим пошуку",
        "Indexed books": "Проіндексовані книги",
        "OCR settings": "Налаштування OCR",
        "Hybrid": "Гібридний",
        "Semantic": "Семантичний",
        "Lexical": "Лексичний",
        "API key": "API-ключ",
        "Page": "Сторінка",
        "Pages": "Сторінки",
    }
    for msgid, translation in expected.items():
        assert catalog.get(msgid).string == translation
