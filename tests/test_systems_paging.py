"""Paging on the systems screen.

The panel is a resistive touchscreen: one contact, no gestures, no wheel.  A
list that only answers to the wheel is a list whose second screenful does not
exist on the hardware this runs on, so these tests are about reachability -
every entity has to be arrivable by tapping ``>`` a finite number of times.
"""

from __future__ import annotations

from pathlib import Path

import pygame
import pytest

from homeinterface.app import App
from homeinterface.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
#: the target panel first: it is the one that runs out of room
SIZES = [(480, 320), (1280, 800)]


def build(size) -> App:
    config = load_config(PROJECT_ROOT / "config" / "app.yaml")
    config["display"]["width"], config["display"]["height"] = size
    config["display"]["fullscreen"] = False
    config["backend"] = {"kind": "mock", "chaos": False}
    app = App(config)
    app._open_window()
    app._build_screens()
    app.screen_index = [s.key for s in app.screens].index("systems")
    return app


def pump(app: App) -> None:
    ctx = app._context()
    title_r, rail_r, content_r, footer_r = app._regions(ctx)
    app._layout_chrome(rail_r, title_r, footer_r, ctx)
    app.screen.ensure_layout(content_r, ctx)
    app._draw(ctx, title_r, rail_r, content_r, footer_r)


def tap(app: App, widget) -> None:
    ctx = app._context()
    pos = widget.rect.center
    app.screen.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos), ctx)
    app.screen.handle(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos), ctx)
    pump(app)


@pytest.fixture(params=SIZES)
def app(request):
    app = build(request.param)
    pump(app)
    yield app
    app.backend.stop()
    pygame.quit()


def test_the_pager_is_there_when_the_list_overruns(app):
    screen = app.screen
    assert screen._max_scroll > 0, "mock backend should not fit on one page"
    assert screen.btn_page_next.visible
    assert screen.btn_page_prev.visible


def test_the_pager_goes_away_when_a_filter_shortens_the_list_enough(app):
    screen = app.screen
    screen._set_filter("climate")
    pump(app)
    if screen._content_height <= screen._tiles_rect.height:
        assert not screen.btn_page_next.visible


def test_next_reaches_the_bottom_in_a_finite_number_of_taps(app):
    """The whole register has to be reachable without a wheel."""
    screen = app.screen
    for _ in range(screen.pages * 2):
        tap(app, screen.btn_page_next)
    assert screen.scroll == pytest.approx(screen._max_scroll)
    assert not screen.btn_page_next.enabled
    assert screen.page + 1 == screen.pages


def test_a_page_turn_moves_by_whole_rows_so_no_tile_is_cut_in_half(app):
    screen = app.screen
    tap(app, screen.btn_page_next)
    assert screen.scroll == pytest.approx(min(screen._page_step, screen._max_scroll))


def test_prev_at_the_top_is_disabled_and_cannot_scroll_past_it(app):
    screen = app.screen
    assert not screen.btn_page_prev.enabled
    tap(app, screen.btn_page_prev)
    assert screen.scroll == 0.0


def test_prev_comes_back_from_the_bottom(app):
    screen = app.screen
    for _ in range(screen.pages * 2):
        tap(app, screen.btn_page_next)
    tap(app, screen.btn_page_prev)
    assert 0 <= screen.scroll < screen._max_scroll
    assert screen.btn_page_next.enabled


def test_tiles_never_draw_under_the_pager(app):
    """The pager takes its strip out of the list, it does not sit on top of it."""
    screen = app.screen
    assert screen._tiles_rect.bottom <= screen.btn_page_next.rect.top
    assert screen._tiles_rect.bottom <= screen._list_rect.bottom


def test_tapping_the_pager_does_not_actuate_the_entity_behind_it(app):
    screen = app.screen
    before = {e: v.state for e, v in app.backend.snapshot().items()}
    tap(app, screen.btn_page_next)
    after = {e: v.state for e, v in app.backend.snapshot().items()}
    assert before == after


def test_a_filter_change_returns_to_the_first_page(app):
    screen = app.screen
    tap(app, screen.btn_page_next)
    assert screen.scroll > 0
    screen._set_filter("sensor")
    pump(app)
    assert screen.scroll == 0.0
