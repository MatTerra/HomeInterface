"""Interactive controls: buttons, toggles, sliders, tab strips.

Touch-first sizing: nothing here is smaller than ~44 design units on its
short edge, and there are no hover-only affordances - hover is a bonus for
mouse users, never the only cue.
"""

from __future__ import annotations

from typing import Callable, Sequence

import pygame

from .. import draw as vd
from ..fonts import blit_text, truncate
from ..theme import RGB, mix
from .base import Pressable, UIContext, Widget


class Button(Pressable):
    def __init__(
        self,
        label: str,
        on_press: Callable[[], None] | None = None,
        *,
        level: str = "info",
        sub: str | None = None,
        compact: bool = False,
    ):
        super().__init__(on_press)
        self.label = label
        self.sub = sub
        self.level = level
        self.compact = compact
        self.active = False

    def _colors(self, ctx: UIContext) -> tuple[RGB, RGB, RGB]:
        t = ctx.theme
        accent = t.status_color(self.level)
        if not self.enabled:
            return mix(t.panel, t.background, 0.4), t.rule, t.inop
        if self.active:
            return mix(t.panel_alt, accent, 0.30), accent, t.text
        if self.is_pressed:
            return mix(t.panel_alt, accent, 0.45), accent, t.text
        hovered = self.rect.collidepoint(ctx.pointer)
        fill = mix(t.panel, t.panel_alt, 1.0 if hovered else 0.35)
        return fill, t.rule_bright if hovered else t.rule, t.text

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        fill, edge, text = self._colors(ctx)
        cut = ctx.u(t.chamfer)
        vd.chamfer_rect(surface, self.rect, fill=fill, cut=cut)
        vd.chamfer_rect(surface, self.rect, outline=edge, width=ctx.px(t.stroke), cut=cut)
        if self.active:
            bar = pygame.Rect(self.rect.left, self.rect.bottom - ctx.px(3), self.rect.width, ctx.px(3))
            pygame.draw.rect(surface, t.status_color(self.level), bar)

        size = ctx.font_px(t.size_small if self.compact else t.size_body)
        # compact buttons are often barely wider than their label (the plan
        # screen's +/-/FIT stack); full padding would truncate them to nothing
        inner = self.rect.width - 2 * ctx.u(6.0 if self.compact else t.pad)
        # the sub-label is the first thing to go when the button gets short
        show_sub = bool(self.sub) and self.rect.height > size * 2.4
        centre_y = self.rect.centery - ctx.u(7) if show_sub else self.rect.centery
        blit_text(surface, ctx.book, truncate(ctx.book, self.label.upper(), size, inner), size, text,
                  (self.rect.centerx, centre_y), anchor="center")
        if show_sub:
            small = ctx.font_px(t.size_micro)
            blit_text(surface, ctx.book, truncate(ctx.book, self.sub.upper(), small, inner), small,
                      t.inop, (self.rect.centerx, self.rect.centery + ctx.u(11)), anchor="center", mono=True)


class ToggleButton(Button):
    """Reflects and drives an on/off entity."""

    def __init__(self, entity_id: str, label: str | None = None, *, sub: str | None = None):
        super().__init__(label or entity_id, None, level="on", sub=sub)
        self.entity_id = entity_id

    def activate(self, ctx: UIContext) -> None:
        ctx.backend.toggle(self.entity_id)

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        entity = ctx.backend.get(self.entity_id)
        self.active = bool(entity and entity.is_on)
        self.level = entity.level if entity else "inop"
        self.enabled = bool(entity and entity.available)
        if entity is not None:
            self.label = entity.name
        super().draw(surface, ctx)

        t = ctx.theme
        state = "ON" if self.active else "OFF"
        if entity is not None and not entity.available:
            state = "N/A"
        blit_text(surface, ctx.book, state, ctx.font_px(t.size_micro),
                  t.status_color(self.level), (self.rect.right - ctx.u(8), self.rect.top + ctx.u(6)),
                  anchor="topright", mono=True)


class Slider(Widget):
    """Linear setter with a tick scale; drag or click-to-set.

    ``on_change`` fires continuously while dragging (cheap, local state) and
    ``on_commit`` fires on release, which is when the service call goes out -
    dragging a dimmer must not flood the backend.
    """

    def __init__(
        self,
        *,
        minimum: float = 0.0,
        maximum: float = 100.0,
        value: float = 0.0,
        label: str = "",
        unit: str = "%",
        vertical: bool = False,
        step: float = 1.0,
        on_change: Callable[[float], None] | None = None,
        on_commit: Callable[[float], None] | None = None,
    ):
        super().__init__()
        self.minimum = minimum
        self.maximum = maximum
        self.value = value
        self.label = label
        self.unit = unit
        self.vertical = vertical
        self.step = step
        self.on_change = on_change
        self.on_commit = on_commit
        self.target: float | None = None  # magenta setpoint marker
        self._dragging = False

    # -- geometry --------------------------------------------------------
    @property
    def _track(self) -> pygame.Rect:
        return self.rect

    def _fraction(self, value: float) -> float:
        span = self.maximum - self.minimum
        return 0.0 if span == 0 else max(0.0, min(1.0, (value - self.minimum) / span))

    def _value_at(self, pos: tuple[int, int]) -> float:
        track = self._track
        if self.vertical:
            f = 1.0 - (pos[1] - track.top) / max(1, track.height)
        else:
            f = (pos[0] - track.left) / max(1, track.width)
        raw = self.minimum + max(0.0, min(1.0, f)) * (self.maximum - self.minimum)
        if self.step > 0:
            raw = round(raw / self.step) * self.step
        return max(self.minimum, min(self.maximum, raw))

    # -- events ----------------------------------------------------------
    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        if not (self.visible and self.enabled):
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
            self._dragging = True
            self._set(self._value_at(event.pos))
            return True
        if event.type == pygame.MOUSEMOTION and self._dragging:
            self._set(self._value_at(event.pos))
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._dragging:
            self._dragging = False
            if self.on_commit:
                self.on_commit(self.value)
            return True
        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(ctx.pointer):
            self._set(self.value + event.y * max(self.step, 1.0))
            if self.on_commit:
                self.on_commit(self.value)
            return True
        return False

    def _set(self, value: float) -> None:
        value = max(self.minimum, min(self.maximum, value))
        if value != self.value:
            self.value = value
            if self.on_change:
                self.on_change(value)

    # -- drawing ---------------------------------------------------------
    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        track = self._track
        accent = t.normal if self.enabled else t.inop
        vd.chamfer_rect(surface, track, fill=mix(t.background, t.panel, 0.7), cut=ctx.u(6))

        f = self._fraction(self.value)
        if self.vertical:
            fill_h = round(track.height * f)
            filled = pygame.Rect(track.left, track.bottom - fill_h, track.width, fill_h)
        else:
            filled = pygame.Rect(track.left, track.top, round(track.width * f), track.height)
        if filled.width > 0 and filled.height > 0:
            vd.chamfer_rect(surface, filled, fill=mix(t.panel_alt, accent, 0.55), cut=ctx.u(6))
        vd.chamfer_rect(surface, track, outline=t.rule_bright, width=ctx.px(t.stroke), cut=ctx.u(6))

        # tick scale along the leading edge
        tick_count = 11
        if self.vertical:
            vd.tick_scale(surface, t.rule_bright, (track.right + ctx.u(3), track.top),
                          track.height, count=tick_count, tick=ctx.u(4), vertical=True,
                          width=ctx.px(1), major_every=5)
        else:
            vd.tick_scale(surface, t.rule_bright, (track.left, track.bottom + ctx.u(3)),
                          track.width, count=tick_count, tick=ctx.u(4), width=ctx.px(1), major_every=5)

        # magenta target marker, ECAM style
        if self.target is not None:
            tf = self._fraction(self.target)
            if self.vertical:
                y = track.bottom - track.height * tf
                pygame.draw.line(surface, t.target, (track.left, y), (track.right, y), ctx.px(t.stroke_bold))
            else:
                x = track.left + track.width * tf
                pygame.draw.line(surface, t.target, (x, track.top), (x, track.bottom), ctx.px(t.stroke_bold))

        if self.label:
            blit_text(surface, ctx.book, self.label.upper(), ctx.font_px(t.size_micro), t.text,
                      (track.left, track.top - ctx.u(5)), anchor="bottomleft")
        readout = f"{self.value:.0f}{self.unit}"
        blit_text(surface, ctx.book, readout, ctx.font_px(t.size_small), t.data,
                  (track.right, track.top - ctx.u(5)), anchor="bottomright", mono=True)


class TabStrip(Widget):
    """Horizontal or vertical page selector."""

    def __init__(self, items: Sequence[tuple[str, str]], on_select: Callable[[str], None],
                 *, vertical: bool = True, gap: float = 8.0):
        super().__init__()
        self.items = list(items)  # (key, label)
        self.on_select = on_select
        self.vertical = vertical
        self.gap = gap
        self.selected: str = self.items[0][0] if self.items else ""
        self._buttons: list[Button] = []
        self._rebuild()

    def _rebuild(self) -> None:
        self._buttons = [
            Button(label, (lambda k=key: self._select(k)), level="info", compact=True)
            for key, label in self.items
        ]

    def set_items(self, items: Sequence[tuple[str, str]]) -> None:
        self.items = list(items)
        if all(key != self.selected for key, _ in self.items) and self.items:
            self.selected = self.items[0][0]
        self._rebuild()
        self.layout(self.rect)

    def _select(self, key: str) -> None:
        self.selected = key
        self.on_select(key)

    def layout(self, rect: pygame.Rect) -> None:
        super().layout(rect)
        if not self._buttons:
            return
        from ..scaling import Box

        box = Box(rect)
        weights = [1.0] * len(self._buttons)
        cells = box.rows(*weights, gap=self.gap) if self.vertical else box.cols(*weights, gap=self.gap)
        for button, cell in zip(self._buttons, cells):
            button.layout(cell.rect)

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        return any(button.handle(event, ctx) for button in self._buttons)

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        for (key, _), button in zip(self.items, self._buttons):
            button.active = key == self.selected
            button.draw(surface, ctx)
