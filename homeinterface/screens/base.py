"""Screen contract.

A screen owns a page of the interface: it lays widgets out into whatever
rectangle the shell gives it, handles events, and draws.  Screens are
re-laid-out on every resize and never cache pixel values.
"""

from __future__ import annotations

import pygame

from ..ui.base import UIContext, WidgetGroup


class Screen:
    #: stable id used by the nav rail and by ``App.show``
    key: str = "screen"
    #: label on the nav button
    title: str = "SCREEN"
    #: one-line caption under the title bar
    subtitle: str = ""

    def __init__(self, app: "object") -> None:
        self.app = app
        self.widgets = WidgetGroup()
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._laid_out_for: tuple[int, int, int, int] | None = None

    # -- lifecycle -------------------------------------------------------
    def on_enter(self, ctx: UIContext) -> None:
        """Called when the screen becomes visible."""

    def on_exit(self, ctx: UIContext) -> None:
        """Called when another screen takes over."""

    def ensure_layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        key = (rect.left, rect.top, rect.width, rect.height)
        if key != self._laid_out_for:
            self.rect = pygame.Rect(rect)
            self.layout(self.rect, ctx)
            self._laid_out_for = key

    def invalidate(self) -> None:
        self._laid_out_for = None

    # -- to implement ----------------------------------------------------
    def layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        raise NotImplementedError

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        self.widgets.draw(surface, ctx)

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        return self.widgets.handle(event, ctx)
