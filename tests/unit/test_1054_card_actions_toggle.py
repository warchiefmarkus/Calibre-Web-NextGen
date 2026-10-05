# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #1054 — the account preference for cover action disclosures.

Browser tests verify the actual dialog and persisted hide/show behavior. These
small checks protect the shared account preference and its forwarding to every
BookCard surface without pinning the retired inline action-row implementation.
"""
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FE = _ROOT / "frontend" / "src"

pytestmark = pytest.mark.unit

_CARD_SURFACES = {
    ("pages", "Catalog.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "Shelf.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "MagicShelfView.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "AdvancedSearch.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "BookDetail.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "GlobalLibrary.tsx"): "hideActions={cardActionsHidden}",
    ("components", "DiscoverSection.tsx"): "hideActions={hideActions}",
    ("components", "MoreByAuthor.tsx"): "hideActions={hideActions}",
}
_STATE_OWNERS = tuple(path for path in _CARD_SURFACES if path[0] == "pages")


def test_preference_is_server_backed_and_defaults_to_showing_actions():
    hook = (_FE / "lib" / "useCardActionsHidden.ts").read_text()
    assert "useNamedPreference(" in hook
    assert "'card_actions_hidden'" in hook
    assert "CARD_ACTIONS_HIDDEN_KEY" in hook
    assert "false" in hook


def test_bookcard_removes_the_row_rather_than_hiding_it():
    """`opacity: 0` is how the hover-reveal works, and it leaves a focusable
    link behind. A user who switched these off must not keep tabbing through two
    invisible controls per card, so the row is not rendered at all."""
    src = (_FE / "components" / "BookCard.tsx").read_text()
    assert "hideActions" in src
    assert "const hasAddAction = membership === 'unowned' && !!onAddToLibrary;" in src
    assert "const hasActionRow = hasAddAction || (!hideActions && (Boolean(readTarget) || quickEdit));" in src


def test_coarse_pointer_cards_carry_no_actions_at_all():
    """Operator ruling 2026-09-12: no disclosure, no hover controls on touch.

    A touch card is the cover plus its info link. The three actions it used to
    carry are reachable by tapping into the book, so nothing on the card may
    re-introduce a per-card action surface — neither the removed "…" disclosure
    nor any replacement for it.
    """
    src = (_FE / "components" / "BookCard.tsx").read_text()
    css = (_FE / "components" / "BookCard.module.css").read_text()

    assert "moreActions" not in css, "the coarse-pointer disclosure CSS must be gone"
    assert "moreActions" not in src, "the coarse-pointer disclosure JSX must be gone"
    assert "aria-expanded" not in src, (
        "a book card must expose no expandable action surface on any pointer"
    )
    assert "MoreHorizontal" not in src, "the disclosure icon import must be gone"

    # The rails released their overflow clipping only to let an open panel
    # escape. With no panel, the release is orphaned and would silently disable
    # the rails' scroll/snap containment for any future aria-expanded control.
    for module in ("DiscoverSection.module.css", "MoreByAuthor.module.css"):
        rail = (_FE / "components" / module).read_text()
        assert "aria-expanded" not in rail, (
            f"{module} still releases rail clipping for a disclosure that no longer exists"
        )

    # Fine pointers are untouched: the established hover/focus reveal stays.
    for selector in (
        ".wrap:hover .removeBtn",
        ".wrap:focus-within .removeBtn",
        ".removeBtn:focus-visible",
        ".wrap:hover .quickEditBtn",
        ".wrap:focus-within .quickEditBtn",
        ".quickEditBtn:focus-visible",
        ".wrap:hover .readNow",
        ".wrap:focus-within .readNow",
        ".readNow:focus-visible",
    ):
        assert selector in css


def test_coarse_pointer_hide_rule_names_the_real_action_classes():
    """The coarse rule must hide the controls, not an unrelated class name.

    Validate both sides of the CSS-module contract: the rule names the three
    concrete controls, and every class it hides is referenced by BookCard.
    """
    src = (_FE / "components" / "BookCard.tsx").read_text()
    css = (_FE / "components" / "BookCard.module.css").read_text()
    hidden = set()
    for selectors, declarations, media in _css_rules(css):
        if media == (_COARSE_MEDIA,) and declarations.get("display") == "none":
            hidden.update(selector.removeprefix(".") for selector in selectors
                          if re.fullmatch(r"\.[A-Za-z_][\w-]*", selector))

    assert hidden == {"readNow", "removeBtn", "quickEditBtn"}, (
        "coarse-pointer hiding must target the three concrete BookCard controls; "
        f"found {sorted(hidden)}"
    )
    unreferenced = sorted(name for name in hidden if f"styles.{name}" not in src)
    assert unreferenced == [], f"CSS hides classes BookCard never renders: {unreferenced}"


@pytest.mark.parametrize("class_name", ("readNow", "removeBtn", "quickEditBtn"))
def test_coarse_pointer_hides_each_action_in_the_effective_cascade(class_name):
    css = (_FE / "components" / "BookCard.module.css").read_text()
    winner = _effective_class_property(css, class_name, "display", coarse=True)
    assert winner is not None and winner[0] == "none", (
        f".{class_name} must resolve to display:none on coarse pointers; winner={winner}"
    )


@pytest.mark.parametrize("class_name", ("readNow", "removeBtn", "quickEditBtn"))
def test_hover_revealed_actions_do_not_disable_pointer_events_at_rest(class_name):
    """Actionability hit-testing happens before Playwright synthesizes hover.

    Coarse pointers remove these controls from layout, so disabling hit-testing
    in the fine-pointer rest state has no remaining job and makes real clicks
    race the hover reveal.
    """
    css = (_FE / "components" / "BookCard.module.css").read_text()
    winner = _effective_class_property(css, class_name, "pointer-events", coarse=False)
    assert winner is None or winner[0] == "auto", (
        f".{class_name} must retain normal pointer hit-testing at rest; winner={winner}"
    )


def test_coarse_pointer_reversal_does_not_touch_primary_actions_or_badges():
    css = (_FE / "components" / "BookCard.module.css").read_text()
    coarse = css.split("@media (any-hover: none), (any-pointer: coarse) {", 1)[1] \
        .split("/* Narrow cards", 1)[0]
    assert ".addToLibrary {\n  opacity: 1;" in css
    assert ".readBadge, .readingBadge, .hiddenBadge, .libraryBadge" in coarse
    assert "min-height: 22px;" in coarse
    assert ".seriesBadge {" in coarse and "height: 22px;" in coarse


def test_touch_cover_chrome_has_no_disclosure_and_keeps_compact_status_badges():
    """Coarse-pointer cards must stay tap-safe without painting action chrome.

    Upstream #2228 removed the touch disclosure entirely after proving that card
    actions belong on the detail page. Keep the fork's compact/ellipsis badge
    treatment so Dense mobile covers still preserve useful status text.
    """
    src = (_FE / "components" / "BookCard.tsx").read_text()
    css = (_FE / "components" / "BookCard.module.css").read_text()
    coarse = css.split("@media (any-hover: none), (any-pointer: coarse) {", 1)[1] \
        .split("/* Narrow cards", 1)[0]

    assert "moreActionsTrigger" not in src
    assert ".moreActionsTrigger" not in css
    assert ".readNow, .removeBtn, .quickEditBtn { display: none; }" in coarse
    assert ".badgeLabel" in css and "text-overflow: ellipsis;" in css
    assert "max-width: 100%;" in css
    assert "@container book-card (max-width: 100px)" in css
    assert ".readBadge svg, .readingBadge svg { display: none; }" in css
    assert src.count("className={styles.badgeLabel}") >= 4


def test_every_book_card_surface_passes_the_live_preference():
    wrong = []
    for parts, expected in _CARD_SURFACES.items():
        if expected not in _FE.joinpath(*parts).read_text():
            wrong.append(f"{parts[-1]} (expected `{expected}`)")
    assert wrong == []


def test_state_owners_read_the_shared_hook():
    missing = [
        parts[-1] for parts in _STATE_OWNERS
        if "useCardActionsHidden(" not in _FE.joinpath(*parts).read_text()
    ]
    assert missing == []


def test_no_book_card_surface_is_missing_from_the_preference_map():
    # CcBrowse has its own real browser preference oracle.
    known = {name for _, name in _CARD_SURFACES} | {"BookCard.tsx", "CcBrowse.tsx"}
    renderers = {
        path.name for path in _FE.rglob("*.tsx")
        if not any(path.name.endswith(suffix)
                   for suffix in (".test.tsx", ".spec.tsx", ".stories.tsx"))
        and "<BookCard" in path.read_text()
    }
    assert renderers <= known, f"unlisted BookCard surface(s): {sorted(renderers - known)}"


def test_toggle_is_exposed_in_catalog_view_settings():
    catalog = (_FE / "pages" / "Catalog.tsx").read_text()
    assert 'data-testid="show-card-actions"' in catalog
    assert "checked={!cardActionsHidden}" in catalog
    assert "t('Show Read now and edit buttons')" in catalog


def test_touch_add_action_and_status_badges_keep_their_minimum_size():
    """The action-panel change must not shrink Global Library's primary Add
    control or the existing read/series badges on touch layouts."""
    css = " ".join((_FE / "components" / "BookCard.module.css").read_text().split())
    assert "@media (hover: none), (pointer: coarse)" in css
    assert ".addToLibrary { opacity: 1; pointer-events: auto;" in css
    assert ".addToLibrary { display: flex; min-height: 44px; }" in css
    assert ".readBadge, .readingBadge, .hiddenBadge, .libraryBadge { min-height: 28px;" in css
    assert ".seriesBadge { min-width: 28px; height: 28px;" in css


def test_action_dialog_strings_are_extractable_and_old_disclosure_is_retired():
    anchors = (_ROOT / "cps" / "spa_strings.py").read_text()
    assert '_("Actions for {title}")' in anchors
    assert '_("More actions for {title}")' not in anchors
    assert '_("Read now")' in anchors
    assert '_("Edit")' in anchors
    assert "_('Close')" in anchors
