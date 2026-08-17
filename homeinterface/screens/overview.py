"""Overview screen - the ECAM upper display equivalent.

House-wide vitals on the left, quick controls in the middle, annunciators and
messages on the right.  Which entities appear here is config-driven
(``overview:`` in ``config/app.yaml``) with sensible fallbacks, so the screen
is useful before anything is configured.
"""

from __future__ import annotations

from typing import Any

import pygame

from ..scaling import Box
from ..ui.base import UIContext
from ..ui.indicators import ArcGauge, BarGauge, EntityTile, MessageStrip, Panel, Readout, StatusLamp
from .base import Screen

DEFAULT_GAUGES: list[dict[str, Any]] = [
    {"entity_id": "sensor.house_power", "label": "POWER", "unit": "W", "max": 8000,
     "caution_above": 5000, "warning_above": 7000},
    {"entity_id": "sensor.outdoor_temperature", "label": "OUTSIDE", "unit": "°C",
     "min": -5, "max": 45, "caution_above": 34},
]


class OverviewScreen(Screen):
    key = "overview"
    title = "VITALS"
    subtitle = "HOUSE STATUS SUMMARY"

    def __init__(self, app):
        super().__init__(app)
        config = dict(getattr(app, "config", {}).get("overview") or {})
        self.gauge_specs = config.get("gauges") or DEFAULT_GAUGES
        self.bar_specs = config.get("bars") or []
        self.quick_ids: list[str] = config.get("quick_controls") or []
        self.lamp_specs = config.get("lamps") or []

        self.gauges = [self._make_gauge(spec) for spec in self.gauge_specs]
        self.bars = [self._make_bar(spec) for spec in self.bar_specs]
        self.messages = MessageStrip(max_lines=6)
        self.lamps: list[StatusLamp] = []
        self.tiles: list[EntityTile] = []

    def _make_gauge(self, spec: dict[str, Any]) -> tuple[str, ArcGauge]:
        gauge = ArcGauge(
            spec.get("label", spec["entity_id"]),
            minimum=float(spec.get("min", 0)),
            maximum=float(spec.get("max", 100)),
            unit=spec.get("unit", ""),
        )
        gauge.caution_above = spec.get("caution_above")
        gauge.warning_above = spec.get("warning_above")
        return spec["entity_id"], gauge

    def _make_bar(self, spec: dict[str, Any]) -> tuple[str, BarGauge]:
        bar = BarGauge(
            spec.get("label", spec["entity_id"]),
            minimum=float(spec.get("min", 0)),
            maximum=float(spec.get("max", 100)),
            unit=spec.get("unit", ""),
        )
        bar.caution_above = spec.get("caution_above")
        return spec["entity_id"], bar

    # -- discovery -------------------------------------------------------
    def _quick_entities(self, ctx: UIContext) -> list[str]:
        if self.quick_ids:
            return self.quick_ids
        entities = ctx.backend.by_domain("light", "switch", "fan")
        entities.sort(key=lambda e: e.entity_id)
        return [e.entity_id for e in entities[:12]]

    def _lamp_entities(self, ctx: UIContext) -> list[dict[str, Any]]:
        if self.lamp_specs:
            return self.lamp_specs
        entities = ctx.backend.by_domain("binary_sensor", "lock", "alarm_control_panel")
        entities.sort(key=lambda e: e.entity_id)
        return [{"entity_id": e.entity_id, "label": e.name} for e in entities[:8]]

    # -- layout ----------------------------------------------------------
    def layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        t = ctx.theme
        gap = ctx.u(t.gap)
        box = Box(rect)
        if ctx.vp.is_wide:
            left, middle, right = box.cols(0.26, 0.46, 0.28, gap=gap)
        elif ctx.vp.landscape:
            # 480x320: three narrower columns still beat stacking, which would
            # leave each band about 120px tall
            left, middle, right = box.cols(0.30, 0.44, 0.26, gap=gap)
        else:
            top, bottom = box.rows(0.52, 0.48, gap=gap)
            left, middle = top.cols(0.42, 0.58, gap=gap)
            right = bottom
        self._left, self._middle, self._right = left.rect, middle.rect, right.rect

        vitals = Panel("VITALS")
        vitals.layout(self._left)
        inner = Box(vitals.inner(ctx))
        gauge_rows = max(1, len(self.gauges))
        bar_weight = 0.32 if self.bars else 0.0
        gauge_box, bar_box = inner.rows(1.0 - bar_weight, bar_weight or 0.0001, gap=gap)
        for (_, gauge), cell in zip(self.gauges, gauge_box.rows(*[1.0] * gauge_rows, gap=gap)):
            gauge.layout(cell.rect)
        if self.bars:
            # fixed pitch rather than an even split, so two bars sit together
            # instead of drifting to opposite ends of the panel
            bar_h = min(ctx.u(46), bar_box.rect.height / len(self.bars))
            pitch = bar_h + gap * 0.5
            for index, (_, bar) in enumerate(self.bars):
                bar.layout(pygame.Rect(bar_box.rect.left, round(bar_box.rect.top + index * pitch),
                                       bar_box.rect.width, round(bar_h)))
        self._vitals = vitals

        controls = Panel("QUICK CONTROLS")
        controls.layout(self._middle)
        ids = self._quick_entities(ctx)
        self.tiles = [EntityTile(entity_id) for entity_id in ids]
        if self.tiles:
            columns = 3 if ctx.vp.is_wide else 2
            rows = max(1, (len(self.tiles) + columns - 1) // columns)
            inner = controls.inner(ctx)
            tile_gap = gap * 0.6
            col_w = (inner.width - tile_gap * (columns - 1)) / columns
            tile_h = min(ctx.u(86), (inner.height - tile_gap * (rows - 1)) / rows)
            for index, tile in enumerate(self.tiles):
                row, col = divmod(index, columns)
                rect = pygame.Rect(
                    round(inner.left + col * (col_w + tile_gap)),
                    round(inner.top + row * (tile_h + tile_gap)),
                    round(col_w),
                    round(tile_h)
                )
                tile.layout(rect)
        self._controls = controls

        status = Panel("STATUS", level="caution")
        status.layout(self._right)
        status_inner = Box(status.inner(ctx))
        lamp_box, msg_box = status_inner.rows(0.55, 0.45, gap=gap)
        specs = self._lamp_entities(ctx)
        self.lamps = [StatusLamp(spec.get("label", spec["entity_id"])) for spec in specs]
        self._lamp_ids = [spec["entity_id"] for spec in specs]
        if self.lamps:
            lamp_h = min(ctx.u(34), lamp_box.rect.height / max(1, len(self.lamps)))
            for i, lamp in enumerate(self.lamps):
                rect = pygame.Rect(lamp_box.rect.left, round(lamp_box.rect.top + i * lamp_h),
                                   lamp_box.rect.width, round(lamp_h))
                lamp.layout(rect)
        self.messages.layout(msg_box.rect)
        self._status = status

    # -- events ----------------------------------------------------------
    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        return any(tile.handle(event, ctx) for tile in self.tiles)

    # -- drawing ---------------------------------------------------------
    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        self._vitals.draw(surface, ctx)
        for entity_id, gauge in self.gauges:
            entity = ctx.backend.get(entity_id)
            gauge.value = entity.number("state") if entity else None
            gauge.draw(surface, ctx)
        for entity_id, bar in self.bars:
            entity = ctx.backend.get(entity_id)
            bar.value = entity.number("state") if entity else None
            bar.draw(surface, ctx)

        self._controls.draw(surface, ctx)
        for tile in self.tiles:
            tile.draw(surface, ctx)

        self._status.draw(surface, ctx)
        for entity_id, lamp in zip(self._lamp_ids, self.lamps):
            entity = ctx.backend.get(entity_id)
            lamp.level = entity.level if entity else "inop"
            lamp.draw(surface, ctx)

        self.messages.lines = [(a.level, a.text) for a in ctx.backend.alerts()]
        self.messages.draw(surface, ctx)
