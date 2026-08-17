"""Read-only instruments: framed panels, readouts, gauges, annunciators."""

from __future__ import annotations

import time
from typing import Callable, Sequence

import pygame

from .. import draw as vd
from ..backend.base import Entity
from ..fonts import blit_text, truncate
from ..theme import RGB, mix
from .base import UIContext, Widget


class Panel(Widget):
    """Titled frame. Content is drawn by ``body`` inside the inner rect."""

    def __init__(self, title: str, body: Callable[[pygame.Surface, pygame.Rect, UIContext], None] | None = None,
                 *, level: str = "info", corner: str = "tlbr"):
        super().__init__()
        self.title = title
        self.body = body
        self.level = level
        self.corner = corner

    def inner(self, ctx: UIContext) -> pygame.Rect:
        pad = ctx.u(ctx.theme.pad)
        header = ctx.u(ctx.theme.size_small) + ctx.u(10)
        return pygame.Rect(
            self.rect.left + pad,
            self.rect.top + header,
            max(1, self.rect.width - 2 * pad),
            max(1, self.rect.height - header - pad),
        )

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        cut = ctx.u(t.chamfer)
        vd.chamfer_rect(surface, self.rect, fill=t.panel, cut=cut, corners=self.corner)
        vd.chamfer_rect(surface, self.rect, outline=t.rule, width=ctx.px(t.stroke), cut=cut, corners=self.corner)

        size = ctx.font_px(t.size_small)
        pad = ctx.u(t.pad)
        title_y = self.rect.top + ctx.u(8)
        accent = t.status_color(self.level)
        pygame.draw.rect(surface, accent,
                         pygame.Rect(self.rect.left + pad, round(title_y + size * 0.15),
                                     ctx.px(3), round(size * 0.75)))
        text_rect = blit_text(surface, ctx.book, self.title.upper(), size, t.text,
                              (self.rect.left + pad + ctx.u(8), title_y), anchor="topleft")
        rule_y = title_y + size * 0.55
        if text_rect.right + ctx.u(10) < self.rect.right - pad:
            pygame.draw.line(surface, t.rule, (text_rect.right + ctx.u(8), rule_y),
                             (self.rect.right - pad, rule_y), ctx.px(1))
        if self.body is not None:
            self.body(surface, self.inner(ctx), ctx)


class Readout(Widget):
    """Big numeric value with a small cyan unit - the ECAM primary readout."""

    def __init__(self, label: str, value: str = "--", unit: str = "", *, level: str = "normal",
                 size: float | None = None):
        super().__init__()
        self.label = label
        self.value = value
        self.unit = unit
        self.level = level
        self.size = size

    @classmethod
    def from_entity(cls, entity: Entity | None, label: str | None = None, *, digits: int = 1) -> "Readout":
        if entity is None:
            return cls(label or "--", "--", "", level="inop")
        number = entity.number("state")
        text = f"{number:.{digits}f}" if number is not None else entity.state.upper()
        return cls(
            label or entity.name,
            text,
            str(entity.attributes.get("unit_of_measurement", "")),
            level=entity.level if entity.domain != "sensor" else ("normal" if entity.available else "inop"),
        )

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        colour = t.status_color(self.level)
        label_px = ctx.font_px(t.size_micro)
        blit_text(surface, ctx.book, truncate(ctx.book, self.label.upper(), label_px, self.rect.width),
                  label_px, t.inop, (self.rect.left, self.rect.top), anchor="topleft")

        value_px = ctx.font_px(self.size or t.size_readout)
        value_rect = blit_text(surface, ctx.book, self.value, value_px, colour,
                               (self.rect.left, self.rect.bottom), anchor="bottomleft", mono=True)
        if self.unit:
            blit_text(surface, ctx.book, self.unit, ctx.font_px(t.size_small), t.data,
                      (value_rect.right + ctx.u(5), value_rect.bottom - ctx.u(3)), anchor="bottomleft")


class ArcGauge(Widget):
    """Circular gauge with a tick arc, green band and magenta target bug."""

    def __init__(self, label: str, *, minimum: float = 0.0, maximum: float = 100.0,
                 unit: str = "", start_deg: float = -135.0, end_deg: float = 135.0):
        super().__init__()
        self.label = label
        self.minimum = minimum
        self.maximum = maximum
        self.unit = unit
        self.start_deg = start_deg
        self.end_deg = end_deg
        self.value: float | None = None
        self.target: float | None = None
        self.caution_above: float | None = None
        self.warning_above: float | None = None

    def _angle(self, value: float) -> float:
        span = self.maximum - self.minimum
        f = 0.0 if span == 0 else max(0.0, min(1.0, (value - self.minimum) / span))
        return self.start_deg + (self.end_deg - self.start_deg) * f

    def _level(self) -> str:
        if self.value is None:
            return "inop"
        if self.warning_above is not None and self.value >= self.warning_above:
            return "warning"
        if self.caution_above is not None and self.value >= self.caution_above:
            return "caution"
        return "normal"

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        side = min(self.rect.width, self.rect.height)
        center = (self.rect.centerx, self.rect.top + side / 2)
        radius = side * 0.42
        stroke = ctx.px(t.stroke_bold)

        vd.arc(surface, t.rule_bright, center, radius, self.start_deg, self.end_deg, width=stroke)
        if self.caution_above is not None:
            vd.arc(surface, t.caution, center, radius, self._angle(self.caution_above),
                   self._angle(self.warning_above if self.warning_above is not None else self.maximum),
                   width=stroke)
        if self.warning_above is not None:
            vd.arc(surface, t.warning, center, radius, self._angle(self.warning_above),
                   self.end_deg, width=stroke)

        for i in range(11):
            angle = self.start_deg + (self.end_deg - self.start_deg) * i / 10
            outer = radius
            inner = radius - ctx.u(10 if i % 5 == 0 else 6)
            pygame.draw.line(surface, t.rule_bright, vd.radial_point(center, inner, angle),
                             vd.radial_point(center, outer, angle), ctx.px(1))

        if self.target is not None:
            angle = self._angle(self.target)
            pygame.draw.line(surface, t.target, vd.radial_point(center, radius - ctx.u(2), angle),
                             vd.radial_point(center, radius + ctx.u(9), angle), ctx.px(t.stroke_bold))

        level = self._level()
        colour = t.status_color(level)
        if self.value is not None:
            blink_off = level == "warning" and not ctx.blink
            if not blink_off:
                vd.arc(surface, colour, center, radius - ctx.u(9), self.start_deg,
                       self._angle(self.value), width=ctx.px(t.stroke_bold + 1))
                vd.pointer(surface, colour, center, self._angle(self.value),
                           inner=-radius * 0.12, outer=radius - ctx.u(12), width=ctx.u(7))
            pygame.draw.circle(surface, t.rule_bright, center, ctx.u(5))

        text = "--" if self.value is None else f"{self.value:.0f}"
        blit_text(surface, ctx.book, text, ctx.font_px(t.size_large), colour,
                  (center[0], center[1] + radius * 0.42), anchor="midtop", mono=True)
        blit_text(surface, ctx.book, f"{self.label.upper()}  {self.unit}".strip(),
                  ctx.font_px(t.size_micro), t.inop, (center[0], self.rect.bottom), anchor="midbottom")


class BarGauge(Widget):
    """Horizontal bar with limits - compact alternative to the arc gauge."""

    def __init__(self, label: str, *, minimum: float = 0.0, maximum: float = 100.0, unit: str = ""):
        super().__init__()
        self.label = label
        self.minimum = minimum
        self.maximum = maximum
        self.unit = unit
        self.value: float | None = None
        self.caution_above: float | None = None

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        label_px = ctx.font_px(t.size_micro)
        # the value is the point of the widget, so it is laid out first and
        # the label gets whatever width is left - on a 100px-wide panel the
        # two would otherwise overprint each other
        text = "--" if self.value is None else f"{self.value:.1f} {self.unit}".strip()
        value_rect = blit_text(surface, ctx.book, text, label_px, t.data,
                               (self.rect.right, self.rect.top), anchor="topright", mono=True)
        room = value_rect.left - self.rect.left - ctx.u(4)
        if room > 0:
            blit_text(surface, ctx.book, truncate(ctx.book, self.label.upper(), label_px, room),
                      label_px, t.text, (self.rect.left, self.rect.top), anchor="topleft")

        track = pygame.Rect(self.rect.left, self.rect.top + label_px + ctx.u(4),
                            self.rect.width, max(ctx.px(6), self.rect.height - label_px - ctx.u(6)))
        vd.chamfer_rect(surface, track, fill=mix(t.background, t.panel, 0.7), cut=ctx.u(4))
        if self.value is not None:
            span = self.maximum - self.minimum or 1.0
            f = max(0.0, min(1.0, (self.value - self.minimum) / span))
            over = self.caution_above is not None and self.value >= self.caution_above
            fill = pygame.Rect(track.left, track.top, round(track.width * f), track.height)
            if fill.width:
                vd.chamfer_rect(surface, fill, fill=t.caution if over else t.normal, cut=ctx.u(4))
        vd.chamfer_rect(surface, track, outline=t.rule, width=ctx.px(1), cut=ctx.u(4))


class StatusLamp(Widget):
    """Small annunciator: chamfered square plus caption, blinks when warning."""

    def __init__(self, caption: str, level: str = "inop"):
        super().__init__()
        self.caption = caption
        self.level = level

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        colour = t.status_color(self.level)
        lit = self.level not in ("inop", "off")
        if self.level == "warning" and not ctx.blink:
            lit = False
        side = min(self.rect.height, ctx.u(18))
        box = pygame.Rect(0, 0, round(side), round(side))
        box.midleft = (self.rect.left, self.rect.centery)
        vd.chamfer_rect(surface, box, fill=colour if lit else mix(t.panel, colour, 0.12), cut=side * 0.35)
        vd.chamfer_rect(surface, box, outline=colour if lit else t.rule_bright, width=ctx.px(1), cut=side * 0.35)
        size = ctx.font_px(t.size_micro)
        blit_text(surface, ctx.book,
                  truncate(ctx.book, self.caption.upper(), size, self.rect.width - side - ctx.u(8)),
                  size, t.text if lit else t.inop,
                  (box.right + ctx.u(7), self.rect.centery), anchor="midleft")


class MessageStrip(Widget):
    """ECAM message list: warnings first, newest last, colour-coded."""

    def __init__(self, max_lines: int = 4):
        super().__init__()
        self.max_lines = max_lines
        self.lines: Sequence[tuple[str, str]] = ()  # (level, text)

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        size = ctx.font_px(t.size_small)
        line_h = size * 1.35
        if not self.lines:
            blit_text(surface, ctx.book, "NO MESSAGES", size, t.inop,
                      (self.rect.left, self.rect.top), anchor="topleft", mono=True)
            return
        for i, (level, text) in enumerate(self.lines[: self.max_lines]):
            colour = t.status_color(level)
            if level == "warning" and not ctx.blink:
                colour = mix(colour, t.background, 0.55)
            y = self.rect.top + i * line_h
            # square bullet, drawn rather than typed: no font-fallback tofu
            side = size * (0.5 if level == "warning" else 0.34)
            marker = pygame.Rect(0, 0, round(side), round(side))
            marker.center = (round(self.rect.left + size * 0.3), round(y + size * 0.55))
            pygame.draw.rect(surface, colour, marker)
            blit_text(surface, ctx.book,
                      truncate(ctx.book, text.upper(), size, self.rect.width - ctx.u(18)),
                      size, colour, (self.rect.left + ctx.u(16), y), anchor="topleft", mono=True)
        overflow = len(self.lines) - self.max_lines
        if overflow > 0:
            blit_text(surface, ctx.book, f"+{overflow} MORE", ctx.font_px(t.size_micro), t.inop,
                      (self.rect.right, self.rect.bottom), anchor="bottomright", mono=True)


class EntityTile(Widget):
    """Compact card for one entity: name, state, and a level stripe."""

    def __init__(self, entity_id: str, *, on_press: Callable[[str], None] | None = None):
        super().__init__()
        self.entity_id = entity_id
        self.on_press = on_press
        self._armed = False

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
            self._armed = True
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._armed:
            self._armed = False
            if self.rect.collidepoint(event.pos):
                if self.on_press:
                    self.on_press(self.entity_id)
                else:
                    ctx.backend.toggle(self.entity_id)
                return True
        return False

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        entity = ctx.backend.get(self.entity_id)
        level = entity.level if entity else "inop"
        colour = t.status_color(level)
        cut = ctx.u(t.chamfer * 0.7)
        hovered = self.rect.collidepoint(ctx.pointer)
        vd.chamfer_rect(surface, self.rect, fill=mix(t.panel, t.panel_alt, 1.0 if hovered else 0.4), cut=cut)
        vd.chamfer_rect(surface, self.rect, outline=t.rule_bright if hovered else t.rule,
                        width=ctx.px(t.stroke), cut=cut)
        pygame.draw.rect(surface, colour,
                         pygame.Rect(self.rect.left, self.rect.top, ctx.px(3), self.rect.height))

        # Type is capped against the tile's own height, not just the viewport
        # scale: a short tile in a dense list would otherwise print its name
        # straight over its state.
        pad = ctx.u(10)
        inner_h = self.rect.height - 2 * ctx.u(6)
        name_px = max(8, min(ctx.font_px(t.size_small), round(inner_h * 0.38)))
        state_px = max(8, min(ctx.font_px(t.size_body), round(inner_h * 0.48)))

        name = entity.name if entity else self.entity_id
        blit_text(surface, ctx.book, truncate(ctx.book, name.upper(), name_px, self.rect.width - 2 * pad),
                  name_px, t.text, (self.rect.left + pad, self.rect.top + ctx.u(6)), anchor="topleft")

        if entity is None:
            text = "MISSING"
        elif entity.domain == "sensor":
            unit = entity.attributes.get("unit_of_measurement", "")
            text = f"{entity.state} {unit}".strip()
        elif entity.domain == "light" and entity.is_on and entity.attributes.get("brightness"):
            text = f"ON {round(float(entity.attributes['brightness']) / 2.55)}%"
        else:
            text = entity.state.upper()
        blit_text(surface, ctx.book, truncate(ctx.book, text, state_px, self.rect.width - 2 * pad),
                  state_px, colour, (self.rect.left + pad, self.rect.bottom - ctx.u(6)),
                  anchor="bottomleft", mono=True)


class Clock(Widget):
    """UTC/local time block, right side of the title bar."""

    def __init__(self, utc: bool = False):
        super().__init__()
        self.utc = utc
        #: on a tiny title bar, drop the seconds and the date line
        self.compact = False

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        now = time.gmtime() if self.utc else time.localtime()
        clock_face = "%H:%M" if self.compact else "%H:%M:%S"
        size = ctx.font_px(t.size_body if self.compact else t.size_large)
        blit_text(surface, ctx.book, time.strftime(clock_face, now), size, t.text,
                  (self.rect.right, self.rect.centery), anchor="midright", mono=True)
        if self.compact:
            return
        blit_text(surface, ctx.book, time.strftime("%d %b %y", now).upper() + ("  UTC" if self.utc else ""),
                  ctx.font_px(t.size_micro), t.inop,
                  (self.rect.right, self.rect.centery + ctx.u(16)), anchor="midright", mono=True)
