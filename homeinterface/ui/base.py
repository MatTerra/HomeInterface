"""Widget base and the per-frame drawing context."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import pygame

from ..backend.base import Backend
from ..fonts import FontBook
from ..scaling import Viewport
from ..theme import Theme


@dataclass
class UIContext:
    """Everything a widget needs to draw itself, rebuilt each frame."""

    theme: Theme
    book: FontBook
    vp: Viewport
    backend: Backend
    now: float = field(default_factory=time.monotonic)
    pointer: tuple[int, int] = (0, 0)
    #: True while a touch/mouse press is held, for pressed-state rendering
    pressed: bool = False

    def u(self, v: float) -> float:
        return self.vp.u(v)

    def px(self, v: float, minimum: int = 1) -> int:
        return self.vp.px(v, minimum)

    def font_px(self, v: float) -> int:
        return self.vp.font_px(v)

    @property
    def blink(self) -> bool:
        """Square wave at the theme's annunciator rate."""
        return math.sin(self.now * math.tau * self.theme.blink_hz) >= 0.0


class Widget:
    """Minimal retained widget: a rect, an event hook and a draw hook."""

    def __init__(self, rect: pygame.Rect | None = None) -> None:
        self.rect = pygame.Rect(rect) if rect else pygame.Rect(0, 0, 0, 0)
        self.enabled = True
        self.visible = True

    def layout(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        """Return True if the event was consumed."""
        return False

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        raise NotImplementedError

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.visible and self.enabled and self.rect.collidepoint(pos)


class Pressable(Widget):
    """Shared press/release bookkeeping for touch and mouse.

    A press only fires on release *inside* the widget, so a mis-touch can be
    dragged off and cancelled - important on a wall panel.
    """

    def __init__(self, on_press: Callable[[], None] | None = None, rect: pygame.Rect | None = None):
        super().__init__(rect)
        self.on_press = on_press
        self._armed = False

    @property
    def is_pressed(self) -> bool:
        return self._armed

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        if not (self.visible and self.enabled):
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
            self._armed = True
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            fired = self._armed and self.rect.collidepoint(event.pos)
            self._armed = False
            if fired:
                self.activate(ctx)
                return True
        return False

    def activate(self, ctx: UIContext) -> None:
        if self.on_press is not None:
            self.on_press()


class WidgetGroup(Widget):
    """Container that forwards events in reverse draw order (topmost first)."""

    def __init__(self, children: list[Widget] | None = None):
        super().__init__()
        self.children: list[Widget] = children or []

    def add(self, widget: Widget) -> Widget:
        self.children.append(widget)
        return widget

    def clear(self) -> None:
        self.children.clear()

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        for child in reversed(self.children):
            if child.visible and child.enabled and child.handle(event, ctx):
                return True
        return False

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        for child in self.children:
            if child.visible:
                child.draw(surface, ctx)
