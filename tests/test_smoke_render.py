"""Headless end-to-end render smoke tests.

Builds the real app against the real config/floorplan.example.yaml and pumps
frames through the same sequence App.run() uses, at a few representative
panel sizes, without ever opening a real window (SDL "dummy" driver, set in
conftest.py).
"""

from __future__ import annotations

from pathlib import Path

import pygame
import pytest

from homeinterface.app import App
from homeinterface.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 480x320 is the target panel and must stay first: it is the size that
# actually breaks when a widget stops fitting.
SIZES = [(480, 320), (1280, 800), (1920, 1080), (2560, 1080)]


def _build_app(width: int, height: int) -> App:
    config = load_config(PROJECT_ROOT / "config" / "app.yaml")
    config["display"]["width"] = width
    config["display"]["height"] = height
    config["display"]["fullscreen"] = False
    config["backend"] = {"kind": "mock", "chaos": False}
    app = App(config)
    app._open_window()
    app._build_screens()
    return app


def _pump_frame(app: App) -> None:
    ctx = app._context()
    title_r, rail_r, content_r, footer_r = app._regions(ctx)
    app._layout_chrome(rail_r, title_r, footer_r, ctx)
    app.screen.ensure_layout(content_r, ctx)
    app._draw(ctx, title_r, rail_r, content_r, footer_r)


def _surface_is_non_blank(surface: pygame.Surface, background) -> bool:
    """True if some sampled pixel differs from the theme background.

    Avoids pygame.surfarray (needs numpy, not a project dependency) by
    sampling a grid of points with get_at() instead of scanning every pixel.
    """
    bg = tuple(background)
    width, height = surface.get_size()
    steps = 40
    for xi in range(steps):
        for yi in range(steps):
            x = min(width - 1, xi * width // steps)
            y = min(height - 1, yi * height // steps)
            if tuple(surface.get_at((x, y)))[:3] != bg:
                return True
    return False


@pytest.mark.parametrize("size", SIZES)
def test_every_screen_renders_without_exception_and_is_non_blank(size):
    app = _build_app(*size)
    try:
        for index in range(len(app.screens)):
            app.screen_index = index
            for _ in range(2):
                _pump_frame(app)
            assert _surface_is_non_blank(app.surface, app.theme.background)
    finally:
        app.backend.stop()
        pygame.quit()


@pytest.mark.parametrize("size", SIZES)
def test_screens_relayout_cleanly_after_resize(size):
    app = _build_app(*size)
    try:
        for index in range(len(app.screens)):
            app.screen_index = index
            _pump_frame(app)

        new_width, new_height = size[1], size[0]  # swap to force a real change
        app._on_resize((new_width, new_height))
        app.surface = pygame.display.set_mode((new_width, new_height), pygame.RESIZABLE)

        for index in range(len(app.screens)):
            app.screen_index = index
            for _ in range(2):
                _pump_frame(app)
            assert _surface_is_non_blank(app.surface, app.theme.background)
    finally:
        app.backend.stop()
        pygame.quit()


@pytest.mark.parametrize("size", SIZES)
def test_screen_switched_by_an_event_draws_in_the_same_frame(size):
    """A tap on the nav rail must not draw a screen that never laid out.

    The frame lays out *before* it drains input, so an event that switches
    screens leaves the incoming screen unlaid — and every screen builds its
    widgets in layout(), so drawing one raises AttributeError. The panel dies
    on the first tap on VITALS. Driving App.tick() is what catches this;
    setting screen_index by hand (as the tests above do) never can.
    """
    app = _build_app(*size)
    try:
        app.tick()
        for _ in range(len(app.screens)):
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB,
                                                 mod=0, unicode="\t", scancode=0))
            app.tick()
            assert _surface_is_non_blank(app.surface, app.theme.background)
    finally:
        app.backend.stop()
        pygame.quit()
