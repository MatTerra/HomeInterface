"""The alternative small-screen shell (``--alternative``).

What these guard is the promise the flag makes: it is *additive* (the stock
screens and chrome are untouched), every target on the 480x320 panel stays a
real touch target, nothing ever overflows its page, and the drill-down reaches
the same backend commands the stock plan screen reaches.
"""

from __future__ import annotations

import pygame
import pytest

from homeinterface.app import ALT_SCREEN_TYPES, SCREEN_TYPES, App
from homeinterface.backend.mock import MockBackend
from homeinterface.config import DEFAULTS, load_config
from homeinterface.floorplan.loader import plan_from_dict
from homeinterface.fonts import FontBook
from homeinterface.scaling import Viewport
from homeinterface.screens.alt import (
    AltHomeScreen,
    AltVitalsScreen,
    DeviceRow,
    PlaceCard,
    PowerChip,
)
from homeinterface.theme import Theme
from homeinterface.ui.base import UIContext

#: the target panel: whatever fits here fits everywhere
PANEL = (480, 320)
#: the shell keeps a title bar and a tab bar; a screen never gets the lot
CONTENT = (466, 224)


class FakeApp:
    def __init__(self, plan, config=None):
        self.theme = Theme()
        self.book = FontBook(self.theme)
        self.plan = plan
        self.config = config or {}


def make_plan(rooms: int = 4, devices_per_room: int = 1, zone: bool = False) -> dict:
    plan = {
        "floors": [{
            "id": "f1",
            "name": "Ground",
            "rooms": [{"id": f"r{i}", "name": f"Room {i}", "rect": [i * 4, 0, 4, 4]}
                      for i in range(rooms)],
            "devices": [{"entity_id": f"light.r{i}_{d}", "at": [i * 4 + 1 + d, 2], "room": f"r{i}"}
                        for i in range(rooms) for d in range(devices_per_room)],
        }],
    }
    if zone:
        plan["zones"] = [{"id": "z1", "name": "Zone One", "rooms": ["r0", "r1"]}]
    return plan


def build(plan_dict: dict, size=PANEL, content=CONTENT):
    plan = plan_from_dict(plan_dict)
    app = FakeApp(plan)
    screen = AltHomeScreen(app)
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*size),
                    backend=MockBackend(entity_ids=plan.entity_ids, chaos=False))
    rect = pygame.Rect(7, 38, content[0], content[1])
    screen.ensure_layout(rect, ctx)
    return screen, ctx, rect


def relayout(screen, ctx, rect):
    """Re-run layout the way the shell does after a state change."""
    screen.ensure_layout(rect, ctx)


def cards(screen) -> list[PlaceCard]:
    return [w for w in screen._widgets if isinstance(w, PlaceCard)]


def rows(screen) -> list[DeviceRow]:
    return [w for w in screen._widgets if isinstance(w, DeviceRow)]


def click(screen, ctx, pos) -> None:
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        screen.handle(pygame.event.Event(kind, button=1, pos=pos), ctx)


# -- the flag is additive ------------------------------------------------
def test_stock_screens_are_untouched_by_the_alternative():
    assert ALT_SCREEN_TYPES != SCREEN_TYPES
    # the systems register is good as it is, and is shared rather than forked
    assert ALT_SCREEN_TYPES[2] is SCREEN_TYPES[2]
    # same keys, so start_screen and the 1-9 shortcuts keep working
    assert [c.key for c in ALT_SCREEN_TYPES] == [c.key for c in SCREEN_TYPES]


def test_alternative_is_off_by_default_and_set_by_the_flag():
    assert DEFAULTS["ui"]["shell"] == "stock"
    assert App(load_config(None)).alternative is False
    config = load_config(None)
    config["ui"] = {"shell": "alt"}
    assert App(config).alternative is True


def test_the_shell_swaps_the_rail_for_a_bottom_bar():
    config = load_config(None)
    config["ui"] = {"shell": "alt"}
    config["display"].update(driver="window", width=PANEL[0], height=PANEL[1])
    app = App(config)
    app._open_window()
    app._build_screens()
    ctx = app._context()
    title, nav, content, _footer = app._regions(ctx)
    assert [type(s) for s in app.screens] == ALT_SCREEN_TYPES
    # a row under the content, not a column beside it
    assert nav.top >= content.bottom
    assert nav.width == content.width
    app._layout_chrome(nav, title, pygame.Rect(0, 0, 0, 0), ctx)
    assert all(nav.contains(button.rect) for button in app.nav)
    # every tab is at least a fingertip wide
    assert min(b.rect.width for b in app.nav) >= 40
    app._close_output()
    pygame.display.quit()


# -- the home stage ------------------------------------------------------
def test_every_place_gets_a_card_and_a_power_chip():
    screen, ctx, _ = build(make_plan(rooms=3))
    assert [c.name for c in cards(screen)] == ["Room 0", "Room 1", "Room 2"]
    assert len([w for w in screen._widgets if isinstance(w, PowerChip)]) == 3


def test_a_zone_is_one_card_however_many_rooms_it_has():
    screen, ctx, _ = build(make_plan(rooms=4, zone=True))
    names = [c.name for c in cards(screen)]
    assert names == ["Zone One", "Room 2", "Room 3"]
    assert cards(screen)[0].is_zone


def test_the_power_chip_commands_the_whole_place_without_drilling_in():
    screen, ctx, rect = build(make_plan(rooms=2, devices_per_room=2))
    chip = [w for w in screen._widgets if isinstance(w, PowerChip)][0]
    click(screen, ctx, chip.rect.center)
    assert screen.mode == "places"  # the card underneath did not fire
    on, total = ctx.backend.group_state(chip.entity_ids)
    assert (on, total) == (2, 2)


def test_cards_never_overlap_and_never_leave_the_page():
    screen, ctx, rect = build(make_plan(rooms=9))
    laid = cards(screen)
    assert laid, "at least one card per page"
    for card in laid:
        assert rect.contains(card.rect)
        assert card.rect.height >= 40  # a fingertip on the reference panel
    for i, a in enumerate(laid):
        for b in laid[i + 1:]:
            assert not a.rect.colliderect(b.rect)


def test_places_page_rather_than_scroll():
    screen, ctx, rect = build(make_plan(rooms=12))
    assert screen.pages > 1
    assert screen.btn_next.visible and screen.btn_prev.visible
    assert not screen.btn_prev.enabled  # nothing before the first page
    first = [c.name for c in cards(screen)]
    click(screen, ctx, screen.btn_next.rect.center)
    relayout(screen, ctx, rect)
    assert screen.page == 1
    assert [c.name for c in cards(screen)] != first


def test_a_page_that_holds_everything_shows_no_pager():
    screen, _ctx, _ = build(make_plan(rooms=2))
    assert screen.pages == 1
    assert not screen.btn_next.visible
    assert screen.btn_next not in screen._widgets


# -- drilling in ---------------------------------------------------------
def test_a_card_opens_the_place_and_back_returns():
    screen, ctx, rect = build(make_plan(rooms=3, devices_per_room=2))
    click(screen, ctx, cards(screen)[1].rect.center)
    relayout(screen, ctx, rect)
    assert screen.mode == "place" and screen.room_id == "r1"
    assert [r.entity_id for r in rows(screen)] == ["light.r1_0", "light.r1_1"]
    click(screen, ctx, screen.btn_back.rect.center)
    relayout(screen, ctx, rect)
    assert screen.mode == "places" and cards(screen)


def test_a_device_row_opens_the_device_and_its_setter_reaches_the_backend():
    screen, ctx, rect = build(make_plan(rooms=2, devices_per_room=1))
    click(screen, ctx, cards(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    click(screen, ctx, rows(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    assert screen.mode == "device" and screen.device_id == "light.r0_0"
    from homeinterface.ui.controls import Slider

    slider = next(w for w in screen._widgets if isinstance(w, Slider))
    slider.on_commit(60.0)
    entity = ctx.backend.get("light.r0_0")
    assert entity.is_on and round(entity.attributes["brightness"] / 2.55) == 60


def test_the_zone_stage_keeps_the_room_scope():
    screen, ctx, rect = build(make_plan(rooms=3, devices_per_room=1, zone=True))
    click(screen, ctx, cards(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    assert screen.zone_id == "z1"
    assert {r.entity_id for r in rows(screen)} == {"light.r0_0", "light.r1_0"}
    chips = [w for w in screen._head if getattr(w, "label", None) == "Room 1"]
    assert chips, "a multi-room zone offers its rooms as scopes"
    click(screen, ctx, chips[0].rect.center)
    relayout(screen, ctx, rect)
    assert screen.scope_room == "r1"
    assert [r.entity_id for r in rows(screen)] == ["light.r1_0"]


def test_the_place_stage_fits_its_column():
    screen, ctx, rect = build(make_plan(rooms=2, devices_per_room=6))
    click(screen, ctx, cards(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    laid = [w for w in screen._widgets if w.visible]
    for widget in laid:
        assert rect.contains(widget.rect), f"{type(widget).__name__} left the page"
    for i, a in enumerate(laid):
        for b in laid[i + 1:]:
            assert not a.rect.colliderect(b.rect), "widgets must not overlap"
    assert all(row.rect.height >= 30 for row in rows(screen))


def test_devices_use_the_panel_width_as_a_grid():
    """A device row is a name and a state; three fit across the panel."""
    screen, ctx, rect = build(make_plan(rooms=2, devices_per_room=6))
    click(screen, ctx, cards(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    laid = rows(screen)
    assert len(laid) == 6, "all six fit once the list is three columns wide"
    lefts = sorted({row.rect.left for row in laid})
    assert len(lefts) == 3, "three columns"
    tops = sorted({row.rect.top for row in laid})
    assert len(tops) == 2, "two lines"
    for top in tops:
        line = sorted((r for r in laid if r.rect.top == top), key=lambda r: r.rect.left)
        assert len(line) == 3
        for a, b in zip(line, line[1:]):
            assert not a.rect.colliderect(b.rect)
        assert all(r.rect.width >= 100 for r in line), "a column still holds a name"


def test_a_narrow_column_falls_back_to_one_device_per_line():
    screen, ctx, rect = build(make_plan(rooms=2, devices_per_room=2),
                              size=(320, 320), content=(200, 224))
    click(screen, ctx, cards(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    assert len({row.rect.left for row in rows(screen)}) == 1


def test_a_place_with_no_devices_still_opens():
    plan = make_plan(rooms=2)
    plan["floors"][0]["devices"] = []
    screen, ctx, rect = build(plan)
    click(screen, ctx, cards(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    assert screen.mode == "place" and not rows(screen)


def test_backspace_walks_back_out():
    screen, ctx, rect = build(make_plan(rooms=2, devices_per_room=1))
    click(screen, ctx, cards(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    click(screen, ctx, rows(screen)[0].rect.center)
    relayout(screen, ctx, rect)
    for expected in ("place", "places"):
        screen.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE), ctx)
        relayout(screen, ctx, rect)
        assert screen.mode == expected


# -- floors --------------------------------------------------------------
def test_floor_chips_appear_only_when_there_is_a_choice():
    from homeinterface.screens.alt import Chip

    screen, _ctx, _ = build(make_plan(rooms=2))
    assert not [w for w in screen._head if isinstance(w, Chip)]

    two = make_plan(rooms=2)
    two["floors"].append({"id": "f2", "name": "Upper", "level": 1,
                          "rooms": [{"id": "u1", "name": "Attic", "rect": [0, 0, 4, 4]}]})
    screen, ctx, rect = build(two)
    chips = [w for w in screen._head if isinstance(w, Chip)]
    assert len(chips) == 2
    click(screen, ctx, chips[0].rect.center)  # the strip reads top-down
    relayout(screen, ctx, rect)
    assert screen.floor_id == "f2"
    assert [c.name for c in cards(screen)] == ["Attic"]


# -- the vitals screen ---------------------------------------------------
@pytest.mark.parametrize("section", ["vitals", "quick", "status"])
def test_vitals_sections_each_own_the_whole_rectangle(section):
    plan = plan_from_dict(make_plan(rooms=2, devices_per_room=4))
    app = FakeApp(plan, {"overview": {}})
    screen = AltVitalsScreen(app)
    screen.section = section
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*PANEL),
                    backend=MockBackend(entity_ids=plan.entity_ids, chaos=False))
    rect = pygame.Rect(7, 38, *CONTENT)
    screen.ensure_layout(rect, ctx)
    assert len(screen._chips) == 3
    widgets = [*screen._chips, *screen.tiles, *screen.lamps,
               *(g for _, g in screen.gauges), *(b for _, b in screen.bars)]
    for widget in widgets:
        assert rect.contains(widget.rect)
    if section == "quick":
        assert screen.tiles and all(t.rect.height >= 30 for t in screen.tiles)


def test_vitals_chips_switch_sections():
    plan = plan_from_dict(make_plan(rooms=1))
    app = FakeApp(plan, {"overview": {}})
    screen = AltVitalsScreen(app)
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*PANEL),
                    backend=MockBackend(entity_ids=plan.entity_ids, chaos=False))
    rect = pygame.Rect(7, 38, *CONTENT)
    screen.ensure_layout(rect, ctx)
    click(screen, ctx, screen._chips[1].rect.center)
    assert screen.section == "quick"
