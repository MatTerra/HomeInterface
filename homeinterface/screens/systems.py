"""Systems screen - every entity the backend exposes, plus link diagnostics.

This is the maintenance page: a scrollable, domain-filtered list of raw
entities.  Useful for finding the entity ids to paste into a floor plan.
"""

from __future__ import annotations

import pygame

from ..fonts import blit_text, truncate
from ..scaling import Box
from ..theme import mix
from ..ui.base import UIContext
from ..ui.controls import Button
from ..ui.indicators import EntityTile, Panel
from .base import Screen

DOMAIN_ORDER = ["light", "switch", "fan", "cover", "climate", "lock", "binary_sensor", "sensor"]

#: short forms for the filter strip when the panel is too narrow to spell
#: "BINARY SENSOR" across nine buttons
DOMAIN_ABBREV = {
    "light": "LGT",
    "switch": "SW",
    "fan": "FAN",
    "cover": "CVR",
    "climate": "CLM",
    "lock": "LCK",
    "binary_sensor": "BIN",
    "sensor": "SNS",
}


class SystemsScreen(Screen):
    key = "systems"
    title = "SYS"
    subtitle = "ENTITY REGISTER / LINK STATUS"

    def __init__(self, app):
        super().__init__(app)
        self.filter: str | None = None
        self.scroll = 0.0
        self.tiles: list[EntityTile] = []
        self._filters: list[Button] = []
        self._list_rect = pygame.Rect(0, 0, 1, 1)
        self._content_height = 0.0
        self._known_revision = -1
        self._columns = 3

    def _set_filter(self, domain: str | None) -> None:
        self.filter = domain
        self.scroll = 0.0
        self._known_revision = -1

    def layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        t = ctx.theme
        gap = ctx.u(t.gap)
        box = Box(rect)
        filter_h = max(ctx.u(t.touch_min), 34)
        filter_box, rest = box.rows(filter_h, rect.height - filter_h - gap, gap=gap)
        if ctx.vp.landscape:
            list_box, diag_box = rest.cols(0.72, 0.28, gap=gap)
        else:
            list_box, diag_box = rest.rows(0.72, 0.28, gap=gap)

        # roughly 78 design units per button is where full domain names stop fitting
        terse = ctx.vp.design_width < 78 * (len(DOMAIN_ORDER) + 1)
        labels = [("ALL", None)] + [
            (DOMAIN_ABBREV[d] if terse else d.upper().replace("_", " "), d) for d in DOMAIN_ORDER
        ]
        self._filters = [Button(name, (lambda d=domain: self._set_filter(d)), compact=True)
                         for name, domain in labels]
        self._filter_domains = [domain for _, domain in labels]
        for button, cell in zip(self._filters, filter_box.cols(*[1.0] * len(labels), gap=gap * 0.5)):
            button.layout(cell.rect)

        self._panel = Panel("ENTITIES")
        self._panel.layout(list_box.rect)
        self._list_rect = self._panel.inner(ctx)
        self._columns = 3 if ctx.vp.is_wide else 2

        self._diag = Panel("LINK", level="info")
        self._diag.layout(diag_box.rect)
        self._known_revision = -1

    def _rebuild_tiles(self, ctx: UIContext) -> None:
        revision = ctx.backend.revision
        if revision == self._known_revision and self.tiles:
            return
        self._known_revision = revision
        entities = list(ctx.backend.snapshot().values())
        if self.filter:
            entities = [e for e in entities if e.domain == self.filter]
        entities.sort(key=lambda e: (DOMAIN_ORDER.index(e.domain) if e.domain in DOMAIN_ORDER else 99,
                                     e.entity_id))
        gap = ctx.u(ctx.theme.gap) * 0.6
        tile_h = max(ctx.u(ctx.theme.touch_min), 34)
        col_w = (self._list_rect.width - gap * (self._columns - 1)) / self._columns
        self.tiles = []
        for index, entity in enumerate(entities):
            row, col = divmod(index, self._columns)
            rect = pygame.Rect(
                round(self._list_rect.left + col * (col_w + gap)),
                round(self._list_rect.top + row * (tile_h + gap)),
                round(col_w), round(tile_h),
            )
            tile = EntityTile(entity.entity_id)
            tile.layout(rect)
            self.tiles.append(tile)
        rows = (len(entities) + self._columns - 1) // self._columns
        self._content_height = rows * (tile_h + gap)

    @property
    def _max_scroll(self) -> float:
        return max(0.0, self._content_height - self._list_rect.height)

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        for button in self._filters:
            if button.handle(event, ctx):
                return True
        if event.type == pygame.MOUSEWHEEL and self._list_rect.collidepoint(ctx.pointer):
            self.scroll = max(0.0, min(self._max_scroll, self.scroll - event.y * ctx.u(60)))
            return True
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and self._list_rect.collidepoint(event.pos):
            shifted = pygame.event.Event(event.type, {**event.dict,
                                                      "pos": (event.pos[0], event.pos[1] + self.scroll)})
            for tile in self.tiles:
                if tile.handle(shifted, ctx):
                    return True
        return False

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        self._rebuild_tiles(ctx)
        for button, domain in zip(self._filters, self._filter_domains):
            button.active = domain == self.filter
            button.draw(surface, ctx)

        self._panel.draw(surface, ctx)
        clip = surface.get_clip()
        surface.set_clip(self._list_rect)
        offset_ctx = ctx
        for tile in self.tiles:
            moved = tile.rect.move(0, -round(self.scroll))
            if moved.bottom < self._list_rect.top or moved.top > self._list_rect.bottom:
                continue
            original = tile.rect
            tile.rect = moved
            tile.draw(surface, offset_ctx)
            tile.rect = original
        surface.set_clip(clip)

        if self._max_scroll > 0:
            track_x = self._list_rect.right - ctx.u(3)
            frac = self._list_rect.height / self._content_height
            bar_h = max(ctx.u(24), self._list_rect.height * frac)
            travel = (self._list_rect.height - bar_h) * (self.scroll / self._max_scroll)
            pygame.draw.rect(surface, t.rule_bright,
                             pygame.Rect(track_x, round(self._list_rect.top + travel),
                                         ctx.px(3), round(bar_h)))

        self._draw_diagnostics(surface, ctx)

    def _draw_diagnostics(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        backend = ctx.backend
        self._diag.level = backend.link.level
        self._diag.draw(surface, ctx)
        inner = self._diag.inner(ctx)
        size = ctx.font_px(t.size_small)
        micro = ctx.font_px(t.size_micro)

        rows: list[tuple[str, str, tuple[int, int, int]]] = [
            ("STATE", backend.link.value.upper(), t.status_color(backend.link.level)),
            ("BACKEND", type(backend).__name__.replace("Backend", "").upper(), t.text),
            ("ENTITIES", str(len(backend.snapshot())), t.data),
            ("ALERTS", str(len(backend.alerts())), t.caution if backend.alerts() else t.normal),
            ("REVISION", str(backend.revision), t.inop),
        ]
        y = inner.top
        for label, value, colour in rows:
            blit_text(surface, ctx.book, label, micro, t.inop, (inner.left, y), anchor="topleft")
            blit_text(surface, ctx.book, value, size, colour, (inner.right, y - ctx.u(2)),
                      anchor="topright", mono=True)
            y += size * 1.6

        error = backend.last_error
        if error:
            blit_text(surface, ctx.book, truncate(ctx.book, error.upper(), micro, inner.width),
                      micro, mix(t.warning, t.text, 0.2), (inner.left, inner.bottom),
                      anchor="bottomleft", mono=True)
