"""Grid presentation of the plan screen's overview stage.

The grid exists because the drawing is true to scale and a lavatory drawn to
scale on a 480x320 panel is a target you have to aim at.  What these tests
guard is that the grid lists the same *units of control* the drawing names -
one card per zone, one per ungrouped room - that every card stays a real touch
target however many places a storey has, and that a card leads into the same
focus stage a tap on the drawing leads into.
"""

from __future__ import annotations

import pygame
import pytest

from homeinterface.backend.mock import MockBackend
from homeinterface.floorplan.loader import plan_from_dict
from homeinterface.fonts import FontBook
from homeinterface.scaling import Viewport
from homeinterface.screens.plan import CARD_MIN_H, CARD_MIN_W, PlanScreen
from homeinterface.theme import Theme
from homeinterface.ui.base import UIContext

#: the target panel: whatever fits here fits everywhere
PANEL = (480, 320)


class FakeApp:
    """The three attributes PlanScreen reads off the app."""

    def __init__(self, plan):
        self.theme = Theme()
        self.book = FontBook(self.theme)
        self.plan = plan


def make_plan(room_count: int = 4, zones: bool = False) -> dict:
    rooms = [
        {"id": f"r{i}", "name": f"Room {i}", "rect": [i * 4, 0, 4, 4]}
        for i in range(room_count)
    ]
    plan = {
        "floors": [{"id": "f1", "name": "Ground", "rooms": rooms,
                    "devices": [{"entity_id": f"light.r{i}", "at": [i * 4 + 2, 2]}
                                for i in range(room_count)]}],
    }
    if zones:
        plan["zones"] = [{"id": "z1", "name": "Zone One", "rooms": ["r0", "r1"]}]
    return plan


def build(plan_dict: dict, size=PANEL) -> tuple[PlanScreen, UIContext]:
    plan = plan_from_dict(plan_dict)
    app = FakeApp(plan)
    screen = PlanScreen(app)
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*size),
                    backend=MockBackend(entity_ids=plan.entity_ids, chaos=False))
    screen.grid_mode = True
    screen.layout(pygame.Rect(0, 0, size[0], size[1]), ctx)
    return screen, ctx


def tap(screen: PlanScreen, ctx: UIContext, widget) -> None:
    pos = widget.rect.center
    screen.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos), ctx)
    screen.handle(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos), ctx)


def test_one_card_per_room_when_no_zones():
    screen, _ = build(make_plan(4))
    assert [c.name for c in screen._cards] == ["Room 0", "Room 1", "Room 2", "Room 3"]


def test_a_zone_takes_one_card_not_one_per_member():
    screen, _ = build(make_plan(4, zones=True))
    names = [c.name for c in screen._cards]
    assert names == ["Zone One", "Room 2", "Room 3"]
    assert [c.is_zone for c in screen._cards] == [True, False, False]


def test_zone_card_carries_every_device_in_the_zone():
    screen, _ = build(make_plan(4, zones=True))
    zone_card = screen._cards[0]
    assert sorted(zone_card.entity_ids) == ["light.r0", "light.r1"]


def test_cards_never_shrink_below_a_touch_target():
    """A storey with more places than a page holds pages them instead."""
    screen, ctx = build(make_plan(24))
    assert screen._grid_pages > 1
    for card in screen._cards:
        assert card.rect.width >= ctx.u(CARD_MIN_W) * 0.9
        assert card.rect.height >= ctx.u(CARD_MIN_H) * 0.9


def test_paging_shows_the_rest_and_never_repeats_a_place():
    screen, ctx = build(make_plan(24))
    seen: list[str] = []
    for _ in range(screen._grid_pages):
        seen += [c.room_id for c in screen._cards]
        tap(screen, ctx, screen.btn_page_next)
        screen.layout(pygame.Rect(0, 0, *PANEL), ctx)
    assert len(seen) == len(set(seen)) == 24


def test_pager_is_absent_when_everything_fits():
    screen, _ = build(make_plan(4))
    assert screen._grid_pages == 1
    assert not screen.btn_page_next.visible


def test_tapping_a_card_enters_the_same_focus_stage_a_tap_on_the_plan_does():
    screen, ctx = build(make_plan(4, zones=True))
    tap(screen, ctx, screen._cards[0])
    assert screen.focused
    assert screen.selected_zone == "z1"
    assert screen.prefer_zone is True
    # focus is the drawing in both presentations, so the grid steps aside
    assert not screen.showing_grid


def test_leaving_focus_returns_to_the_grid_not_to_the_drawing():
    screen, ctx = build(make_plan(4))
    tap(screen, ctx, screen._cards[0])
    screen._exit_focus()
    screen.layout(pygame.Rect(0, 0, *PANEL), ctx)
    assert screen.showing_grid
    assert screen._cards


def test_toggle_switches_presentation_and_keeps_the_floor():
    screen, ctx = build(make_plan(4))
    tap(screen, ctx, screen.btn_view)
    screen.layout(pygame.Rect(0, 0, *PANEL), ctx)
    assert not screen.grid_mode
    assert not screen._cards  # the drawing owns the rectangle again
    assert screen.floor_id == "f1"


def test_the_drawing_keeps_its_zoom_controls_and_the_grid_does_not():
    screen, ctx = build(make_plan(4))
    assert not screen.btn_zoom_in.visible
    tap(screen, ctx, screen.btn_view)
    screen.layout(pygame.Rect(0, 0, *PANEL), ctx)
    assert screen.btn_zoom_in.visible


def test_grid_swallows_taps_that_would_otherwise_pan_the_drawing():
    """No drawing means nothing to pan: a drag on the empty page is inert."""
    screen, ctx = build(make_plan(4))
    empty = (screen._plan_rect.centerx, screen._plan_rect.bottom - 2)
    before = screen.pan
    screen.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=empty), ctx)
    screen.handle(pygame.event.Event(pygame.MOUSEMOTION, pos=(empty[0] + 40, empty[1]),
                                     rel=(40, 0), buttons=(0, 0, 1)), ctx)
    assert screen.pan == before


@pytest.mark.parametrize("size", [(480, 320), (800, 480), (1920, 1080), (2560, 1080)])
def test_cards_stay_inside_the_plan_rectangle_at_every_panel_size(size):
    screen, _ = build(make_plan(9, zones=True), size=size)
    for card in screen._cards:
        assert screen._plan_rect.contains(card.rect), card.name
