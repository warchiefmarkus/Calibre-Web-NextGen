# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Moon+ Reader position-file codec and book-position converters.

Reverse-engineered from Moon+ Reader 10.5 (com.flyersoft.moonreaderp):
``.po`` contains ``deviceId*chapter@split#offset:percent%`` for reflowable
books and ``deviceId*page:percent%`` for PDF. The leading value is a device
identifier used to suppress self-echoes; it is not a timestamp.

For reflowable books ``offset`` is relative to the rendered split fragment,
not to the whole chapter. Moon+ selects a split threshold of 150k, 400k or
1M HTML characters from the device memory class. Existing remote locators are
therefore used to infer the active threshold before writing a new position.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import html
import os
import re
import zipfile
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


_POSITION_RE = re.compile(
    r"^\s*(?P<device>[^*|\r\n]{1,128})\*"
    r"(?P<chapter>-?\d+)"
    r"(?:@(?P<split>-?\d+)#(?P<offset>-?\d+))?"
    r":(?P<percentage>\d+(?:[.,]\d+)?)%"
    r"(?:\|(?P<extra>-?\d+))?\s*$"
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
MOON_SPLIT_SIZES = (150_000, 400_000, 1_000_000)
DEFAULT_MOON_SPLIT_SIZE = 1_000_000


class MoonLocatorError(ValueError):
    pass


@dataclass(frozen=True)
class MoonPosition:
    raw: str
    device_id: str
    chapter: int
    split_index: int | None
    offset: int
    percentage: float
    extra: int | None = None


@dataclass(frozen=True)
class MoonChapter:
    index: int
    text: str
    source_html: str | None = None

    @property
    def size(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class MoonMappedPosition:
    chapter: int
    split_index: int | None
    offset: int
    percentage: float
    matched_anchor: bool = False


def parse_position(value: bytes | str, *, max_bytes: int = 64 * 1024) -> MoonPosition:
    if isinstance(value, bytes):
        if len(value) > max_bytes:
            raise MoonLocatorError("Moon+ position file is too large.")
        try:
            text = value.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MoonLocatorError("Moon+ position file is not UTF-8.") from exc
    else:
        text = str(value)
    match = _POSITION_RE.fullmatch(text)
    if match is None:
        raise MoonLocatorError("Moon+ position file has an unsupported format.")
    percentage = float(match.group("percentage").replace(",", "."))
    if not 0 <= percentage <= 100:
        raise MoonLocatorError("Moon+ percentage is outside 0-100.")
    split = match.group("split")
    offset = match.group("offset")
    extra = match.group("extra")
    return MoonPosition(
        raw=text.strip(),
        device_id=match.group("device"),
        chapter=int(match.group("chapter")),
        split_index=int(split) if split is not None else None,
        offset=int(offset) if offset is not None else int(match.group("chapter")),
        percentage=percentage,
        extra=int(extra) if extra is not None else None,
    )


def _percentage_text(value: float) -> str:
    # Moon+ normally writes one decimal through T.getPercentStr2.
    return f"{max(0.0, min(100.0, float(value))):.1f}"


def serialize_position(
    *, device_id: str, chapter: int, split_index: int | None,
    offset: int, percentage: float, extra: int | None = None,
) -> str:
    device = str(device_id or "").strip()
    if not device or "*" in device or "|" in device or len(device) > 128:
        raise MoonLocatorError("Moon+ device identifier is invalid.")
    if split_index is None:
        locator = str(int(chapter))
    else:
        locator = f"{int(chapter)}@{int(split_index)}#{max(0, int(offset))}"
    suffix = f"|{int(extra)}" if extra is not None else ""
    return f"{device}*{locator}:{_percentage_text(percentage)}%{suffix}"


def normalize_text(value: str) -> str:
    value = html.unescape(str(value or "")).replace("\xa0", " ")
    value = _TAG_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _primary_fb2_body(root: ET.Element) -> ET.Element | None:
    bodies = []
    for node in root.iter():
        if _local_name(node.tag) != "body":
            continue
        bodies.append(node)
        if not node.attrib.get("name"):
            return node
    # Valid FB2 files may name their only/main body. Moon+ accepts those.
    return bodies[0] if bodies else None


def _iter_sections(body: ET.Element) -> Iterable[ET.Element]:
    for node in body.iter():
        if node is not body and _local_name(node.tag) == "section":
            yield node


def _section_without_nested_sections(section: ET.Element) -> ET.Element:
    clone = copy.deepcopy(section)
    for parent in list(clone.iter()):
        for child in list(parent):
            if _local_name(child.tag) == "section":
                parent.remove(child)
    return clone


def _node_text(node: ET.Element) -> str:
    return normalize_text(" ".join(part for part in node.itertext() if part))


def _strip_xml_namespaces(node: ET.Element) -> None:
    for item in node.iter():
        item.tag = _local_name(item.tag)
        item.attrib = {_local_name(key): value for key, value in item.attrib.items()}


def _fb2_to_moon_html(value: str) -> str:
    """Apply the structural conversions from Fb2.reverseFb2Tag()."""
    result = value
    replacements = (
        (r"<poem>", '<div class="poem">'), (r"</poem>", "</div>"),
        (r"<cite>", '<div class="cite">'), (r"</cite>", "</div>"),
        (r"<text-author>", '<div class="text-author">'),
        (r"</text-author>", "</div>"),
        (r"<(\/?)emphasis>", r"<\1i>"),
        (r"<stanza>", ""), (r"</stanza>", "<br>"),
        (r"<a l:", "<a "),
        (r"<v(?:\s+.*?)?>", "&nbsp;&nbsp;&nbsp;"), (r"</v>", "<br>"),
        (r"<empty-line\s*/>", "<br><br>"),
        (r"<empty-line>", "<br><br>"), (r"</empty-line>", ""),
        (r"<subtitle>", '<h5 class="subtitle">'), (r"</subtitle>", "</h5>"),
        (r"<epigraph>", '<div class="epigraph">'), (r"</epigraph>", "</div>"),
        (r"<(\/?)strikethrough>", r"<\1strike>"),
        (r"<(\/?)title(?:\s+.*?)?>", r"<\1h5>"),
    )
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(
        r"<b>(?:\s|\u3000|&nbsp;)*</b>", "", result, flags=re.IGNORECASE,
    )
    result = re.sub(
        r"<p>(?:\s|\u3000|&nbsp;)*</p>", "", result, flags=re.IGNORECASE,
    )
    return result


def _section_source_html(section: ET.Element) -> str:
    clone = _section_without_nested_sections(section)
    _strip_xml_namespaces(clone)
    content = "".join(
        ET.tostring(child, encoding="unicode", method="html") for child in clone
    )
    return _fb2_to_moon_html(content)


def _read_fb2_bytes(path: str) -> bytes:
    lower = path.casefold()
    if lower.endswith((".fbz", ".fb2.zip", ".zip")):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.casefold().endswith(".fb2")]
            if not names:
                raise MoonLocatorError("FB2 archive contains no .fb2 document.")
            return archive.read(names[0])
    return Path(path).read_bytes()


_FOLIATE_FB2_BODY_CHILDREN = frozenset({"image", "title", "epigraph", "section"})


def fb2_chapters_and_foliate_sections(path: str) -> tuple[list[MoonChapter], dict[int, int]]:
    """Parse Moon chapters and map each flattened chapter to Foliate's section.

    Moon+ numbers every nested FB2 ``<section>`` as a separate chapter. Foliate
    instead creates one reader section per convertible *direct* child of the main
    ``<body>`` and keeps nested sections inside that same DOM document. The two
    indexes therefore diverge as soon as a top-level section contains children.
    """
    try:
        root = ET.fromstring(_read_fb2_bytes(path))
    except (ET.ParseError, OSError, zipfile.BadZipFile) as exc:
        raise MoonLocatorError("Could not parse the FB2 document.") from exc
    body = _primary_fb2_body(root)
    if body is None:
        raise MoonLocatorError("FB2 primary body was not found.")

    section_to_foliate: dict[int, int] = {}
    foliate_index = 0
    for child in list(body):
        if _local_name(child.tag) not in _FOLIATE_FB2_BODY_CHILDREN:
            continue
        for node in child.iter():
            if _local_name(node.tag) == "section":
                section_to_foliate[id(node)] = foliate_index
        foliate_index += 1

    chapters: list[MoonChapter] = []
    chapter_to_foliate: dict[int, int] = {}
    for index, section in enumerate(_iter_sections(body)):
        text = _node_text(_section_without_nested_sections(section))
        if text:
            chapters.append(MoonChapter(
                index=index, text=text, source_html=_section_source_html(section),
            ))
            mapped = section_to_foliate.get(id(section))
            if mapped is not None:
                chapter_to_foliate[index] = mapped
    return chapters, chapter_to_foliate


def fb2_chapters(path: str) -> list[MoonChapter]:
    return fb2_chapters_and_foliate_sections(path)[0]


def _epub_rootfile(archive: zipfile.ZipFile) -> str:
    container = ET.fromstring(archive.read("META-INF/container.xml"))
    for node in container.iter():
        if _local_name(node.tag) == "rootfile" and node.attrib.get("full-path"):
            return node.attrib["full-path"]
    raise MoonLocatorError("EPUB package document was not found.")


def epub_chapters(path: str) -> list[MoonChapter]:
    try:
        with zipfile.ZipFile(path) as archive:
            opf_name = _epub_rootfile(archive)
            opf = ET.fromstring(archive.read(opf_name))
            manifest = {
                node.attrib.get("id"): node.attrib.get("href")
                for node in opf.iter() if _local_name(node.tag) == "item"
            }
            spine = [
                node.attrib.get("idref") for node in opf.iter()
                if _local_name(node.tag) == "itemref" and node.attrib.get("idref")
            ]
            base = os.path.dirname(opf_name)
            chapters: list[MoonChapter] = []
            for index, item_id in enumerate(spine):
                href = manifest.get(item_id)
                if not href:
                    continue
                name = os.path.normpath(
                    os.path.join(base, href.split("#", 1)[0])
                ).replace("\\", "/")
                try:
                    document = ET.fromstring(archive.read(name))
                except (KeyError, ET.ParseError):
                    continue
                for node in list(document.iter()):
                    if _local_name(node.tag) in {"script", "style", "svg"}:
                        node.clear()
                text = _node_text(document)
                if text:
                    source = copy.deepcopy(document)
                    _strip_xml_namespaces(source)
                    chapters.append(MoonChapter(
                        index=index,
                        text=text,
                        source_html=ET.tostring(
                            source, encoding="unicode", method="html",
                        ),
                    ))
            return chapters
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise MoonLocatorError("Could not parse the EPUB document.") from exc


def chapters_and_foliate_sections(
    path: str, fmt: str,
) -> tuple[list[MoonChapter], dict[int, int]]:
    name = str(fmt or "").upper()
    if name in {"FB2", "FBZ"} or path.casefold().endswith((".fb2", ".fbz", ".fb2.zip")):
        return fb2_chapters_and_foliate_sections(path)
    if name in {"EPUB", "KEPUB"} or path.casefold().endswith((".epub", ".kepub")):
        chapters = epub_chapters(path)
        return chapters, {chapter.index: chapter.index for chapter in chapters}
    return [], {}


def chapters_for_book(path: str, fmt: str) -> list[MoonChapter]:
    return chapters_and_foliate_sections(path, fmt)[0]


def _chapter_total(chapters: list[MoonChapter]) -> int:
    return max(1, sum(max(1, chapter.size) for chapter in chapters))


def _moon_clean_html(value: str) -> str:
    value = value.replace("\u00ad", "")
    if len(value) > 100_000:
        return value
    value = value.strip()
    while value.endswith("<br>") or value.endswith("\u3000") or value.endswith("\xa0"):
        value = value[:-4] if value.endswith("<br>") else value[:-1]
    return value


def _find_split_end(source: str, target: int, split_size: int) -> int:
    if target > len(source) - 1:
        return len(source)
    end = -1
    for marker in ("<p", "<P", "<div", "<DIV"):
        end = source.find(marker, target)
        if end != -1:
            break
    if end == -1:
        close = source.find(">", target)
        end = close + 1 if close != -1 else len(source)
    if end > target + (split_size // 5):
        return len(source)
    return end


def _moon_split_html(source: str, split_size: int) -> list[str]:
    """Port of A.createSplitHtmls for Moon+ HTML ebook content."""
    split_size = max(1, int(split_size))
    if len(source) <= split_size:
        return [_moon_clean_html(source)]
    result: list[str] = []
    start = 0
    carry_div: str | None = None
    while start < len(source):
        target = start + split_size
        end = _find_split_end(source, target, split_size)
        if end <= start:
            end = min(len(source), start + split_size)
        fragment = source[start:end]
        if len(fragment) > split_size * 5:
            fragment = fragment[:split_size * 2]
        fragment = _moon_clean_html(fragment)
        visible = normalize_text(fragment)
        if len(fragment) >= 200 or visible:
            if carry_div:
                fragment = carry_div + fragment
            result.append(fragment)
            lower = fragment.lower()
            div_start = lower.rfind("<div")
            if div_start != -1 and lower.rfind("</div") < div_start:
                tag_end = fragment.find(">", div_start)
                carry_div = fragment[div_start:tag_end + 1] if tag_end != -1 else None
            else:
                carry_div = None
        start = end
    if not result:
        result = [_moon_clean_html(source)]
    if len(source) < 2_000_000 and len(result) > 1:
        count = len(result)
        for index in range(count - 1):
            result[index] += (
                f'<br/><span align="right"><font color=#6060EE>'
                f'[{index + 1}/{count}]</font></span>'
            )
    return result


def moon_split_texts(chapter: MoonChapter, split_size: int) -> list[str]:
    source = chapter.source_html if chapter.source_html is not None else chapter.text
    rendered = [normalize_text(fragment) for fragment in _moon_split_html(source, split_size)]
    return [item for item in rendered if item] or [chapter.text]


def fraction_from_locator(
    chapters: list[MoonChapter], chapter: int, offset: int, *,
    split_index: int = 0, split_size: int = DEFAULT_MOON_SPLIT_SIZE,
) -> float:
    if not chapters:
        return 0.0
    by_index = {item.index: item for item in chapters}
    current = by_index.get(int(chapter))
    if current is None:
        current = min(chapters, key=lambda item: abs(item.index - int(chapter)))
    splits = moon_split_texts(current, split_size)
    selected = max(0, min(len(splits) - 1, int(split_index)))
    rendered_total = max(1, sum(max(1, len(item)) for item in splits))
    rendered_position = sum(max(1, len(item)) for item in splits[:selected])
    rendered_position += max(0, min(len(splits[selected]), int(offset)))
    chapter_position = current.size * rendered_position / rendered_total
    prior = sum(max(1, item.size) for item in chapters if item.index < current.index)
    position = prior + chapter_position
    return max(0.0, min(1.0, position / _chapter_total(chapters)))


def anchor_from_locator(
    chapters: list[MoonChapter], position: MoonPosition, *, max_length: int = 180,
) -> str | None:
    """Return visible text beginning at an exact Moon reflowable locator.

    Moon and Foliate use different progress fractions.  This anchor lets the
    browser resolve Moon's chapter/split/character locator against Foliate's
    own DOM and produce a native CFI without pretending the percentages share
    the same coordinate system.
    """
    if not chapters or position.split_index is None:
        return None
    current = next((item for item in chapters if item.index == position.chapter), None)
    if current is None:
        return None
    split_size = infer_split_size(chapters, position)
    splits = moon_split_texts(current, split_size)
    if not 0 <= int(position.split_index) < len(splits):
        return None
    text = splits[int(position.split_index)]
    start = max(0, min(len(text), int(position.offset)))
    anchor = normalize_text(text[start:start + max(32, int(max_length))]).strip()
    return anchor or None


def infer_split_size(chapters: list[MoonChapter], position: MoonPosition | None) -> int:
    if not chapters or position is None or position.split_index is None:
        return DEFAULT_MOON_SPLIT_SIZE
    target = max(0.0, min(1.0, position.percentage / 100.0))
    current = next((item for item in chapters if item.index == position.chapter), None)
    if current is None:
        return DEFAULT_MOON_SPLIT_SIZE
    ranked = []
    for size in MOON_SPLIT_SIZES:
        splits = moon_split_texts(current, size)
        valid = 0 <= position.split_index < len(splits)
        mapped = fraction_from_locator(
            chapters, position.chapter, position.offset,
            split_index=position.split_index, split_size=size,
        )
        ranked.append((0 if valid else 1, abs(mapped - target), -size, size))
    return min(ranked)[-1]


def _anchor_candidates(anchor: str) -> list[str]:
    value = normalize_text(anchor)
    candidates = []
    for length in (240, 160, 100, 64, 36):
        if len(value) >= length:
            candidates.append(value[:length])
    if value and value not in candidates:
        candidates.append(value)
    return candidates


def map_fraction_to_moon(
    chapters: list[MoonChapter], fraction: float, *, anchor_text: str | None = None,
    split_size: int = DEFAULT_MOON_SPLIT_SIZE,
) -> MoonMappedPosition:
    if not chapters:
        pct = max(0.0, min(100.0, float(fraction) * 100.0))
        return MoonMappedPosition(0, 0, 0, pct, False)
    total = _chapter_total(chapters)
    if anchor_text:
        for candidate in _anchor_candidates(anchor_text):
            for chapter in chapters:
                splits = moon_split_texts(chapter, split_size)
                for split_index, split_text in enumerate(splits):
                    offset = split_text.find(candidate)
                    if offset >= 0:
                        percentage = 100.0 * fraction_from_locator(
                            chapters, chapter.index, offset,
                            split_index=split_index, split_size=split_size,
                        )
                        return MoonMappedPosition(
                            chapter.index, split_index, offset, percentage, True,
                        )
    target = max(
        0,
        min(total - 1, int(round(max(0.0, min(1.0, float(fraction))) * total))),
    )
    consumed = 0
    for chapter in chapters:
        size = max(1, chapter.size)
        if target < consumed + size:
            pure_offset = max(0, min(chapter.size, target - consumed))
            splits = moon_split_texts(chapter, split_size)
            rendered_total = max(1, sum(max(1, len(item)) for item in splits))
            rendered_target = int(round(
                (pure_offset / max(1, chapter.size)) * rendered_total
            ))
            split_index = 0
            offset = rendered_target
            for index, split_text in enumerate(splits):
                if offset <= len(split_text) or index == len(splits) - 1:
                    split_index = index
                    offset = max(0, min(len(split_text), offset))
                    break
                offset -= max(1, len(split_text))
            percentage = 100.0 * fraction_from_locator(
                chapters, chapter.index, offset,
                split_index=split_index, split_size=split_size,
            )
            return MoonMappedPosition(
                chapter.index, split_index, offset, percentage, False,
            )
        consumed += size
    last = chapters[-1]
    splits = moon_split_texts(last, split_size)
    return MoonMappedPosition(
        last.index, len(splits) - 1, len(splits[-1]), 100.0, False,
    )


def map_book_position(
    path: str, fmt: str, fraction: float, *, anchor_text: str | None = None,
    page: int | None = None, remote_position: MoonPosition | None = None,
    split_size: int | None = None,
) -> MoonMappedPosition:
    if str(fmt or "").upper() == "PDF":
        page_number = max(0, int(page or 0))
        return MoonMappedPosition(
            page_number, None, page_number,
            max(0.0, min(100.0, float(fraction) * 100.0)), False,
        )
    chapters = chapters_for_book(path, fmt)
    active_split_size = split_size or infer_split_size(chapters, remote_position)
    return map_fraction_to_moon(
        chapters, fraction, anchor_text=anchor_text, split_size=active_split_size,
    )
