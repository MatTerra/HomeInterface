"""Systems screen - every entity the backend exposes, plus link diagnostics.

This is the maintenance page: a paged, domain-filtered list of raw entities.
Useful for finding the entity ids to paste into a floor plan.

The list moves by whole pages, on buttons.  The wheel still scrolls it freely
for mouse users, but the wheel is not available on the panel this runs on: a
resistive touchscreen reports one contact and no gesture, so anything past the
first screenful has to be reachable by tapping something.
"""

from __future__ import annotations

import math

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
        #: the list rectangle minus the pager strip, when there is one
        self._tiles_rect = pygame.Rect(0, 0, 1, 1)
        self._content_height = 0.0
        self._known_revision = -1
        self._columns = 3
        self._page_step = 1.0
        self.btn_page_prev = Button("<", lambda: self._turn_page(-1), compact=True)
        self.btn_page_next = Button(">", lambda: self._turn_page(1), compact=True)
        self.btn_page_prev.visible = self.btn_page_next.visible = False

    def _set_filter(self, domain: str | None) -> None:
        self.filter = domain
        self.scroll = 0.0
        self._known_revision = -1

    # -- paging ----------------------------------------------------------
    @property
    def page(self) -> int:
        """Which page the current offset sits on, counting from zero.

        Rounded *up*: the last page is a short one - the offset stops at
        ``_max_scroll``, not at a multiple of the step - and an operator who
        has reached the bottom is on the last page, not still on the one
        before it.
        """
        if self._page_step <= 0:
            return 0
        return math.ceil(self.scroll / self._page_step - 1e-6)

    @property
    def pages(self) -> int:
        """How many taps of ``>`` the list is deep, plus the page you start on."""
        if self._page_step <= 0:
            return 1
        return 1 + math.ceil(self._max_scroll / self._page_step - 1e-6)

    def _turn_page(self, step: int) -> None:
        """Move by whole pages, snapping a wheel-scrolled offset onto the grid."""
        target = (self.page + step) * self._page_step
        self.scroll = max(0.0, min(self._max_scroll, target))

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
        pitch = tile_h + gap
        rows = (len(entities) + self._columns - 1) // self._columns
        self._content_height = rows * pitch

        # The pager only exists when the list overruns, and it costs the list
        # the strip it stands in - which can itself push one more row out of
        # view, so the fit is decided against the reduced height.
        pager_h = max(ctx.u(ctx.theme.touch_min * 0.8), 30)
        self._tiles_rect = pygame.Rect(self._list_rect)
        if self._content_height > self._tiles_rect.height:
            self._tiles_rect.height = max(round(pitch), self._tiles_rect.height - round(pager_h + gap))
            self._layout_pager(ctx, pager_h, gap)
        self.btn_page_prev.visible = self.btn_page_next.visible = (
            self._content_height > self._tiles_rect.height
        )

        per_page = max(1, int((self._tiles_rect.height + gap) // pitch))
        self._page_step = per_page * pitch
        self.scroll = max(0.0, min(self._max_scroll, self.scroll))
        self.btn_page_prev.enabled = self.scroll > 0
        self.btn_page_next.enabled = self.scroll < self._max_scroll

        col_w = (self._tiles_rect.width - gap * (self._columns - 1)) / self._columns
        self.tiles = []
        for index, entity in enumerate(entities):
            row, col = divmod(index, self._columns)
            rect = pygame.Rect(
                round(self._tiles_rect.left + col * (col_w + gap)),
                round(self._tiles_rect.top + row * pitch),
                round(col_w), round(tile_h),
            )
            tile = EntityTile(entity.entity_id)
            tile.layout(rect)
            self.tiles.append(tile)

    def _layout_pager(self, ctx: UIContext, height: float, gap: float) -> None:
        """Two buttons on the strip below the list, right-aligned.

        Right is where the thumb rests on a wall panel, and it leaves the left
        of the strip for the page counter.
        """
        width = max(ctx.u(ctx.theme.touch_min * 1.4), 48)
        top = round(self._list_rect.bottom - height)
        self.btn_page_prev.layout(pygame.Rect(
            round(self._list_rect.right - width * 2 - gap), top, round(width), round(height)))
        self.btn_page_next.layout(pygame.Rect(
            round(self._list_rect.right - width), top, round(width), round(height)))

    @property
    def _max_scroll(self) -> float:
        return max(0.0, self._content_height - self._tiles_rect.height)

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        for button in self._filters:
            if button.handle(event, ctx):
                return True
        for button in (self.btn_page_prev, self.btn_page_next):
            if button.visible and button.enabled and button.handle(event, ctx):
                return True
        if event.type == pygame.MOUSEWHEEL and self._list_rect.collidepoint(ctx.pointer):
            self.scroll = max(0.0, min(self._max_scroll, self.scroll - event.y * ctx.u(60)))
            return True
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and self._tiles_rect.collidepoint(event.pos):
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
        surface.set_clip(self._tiles_rect)
        offset_ctx = ctx
        for tile in self.tiles:
            moved = tile.rect.move(0, -round(self.scroll))
            if moved.bottom < self._tiles_rect.top or moved.top > self._tiles_rect.bottom:
                continue
            original = tile.rect
            tile.rect = moved
            tile.draw(surface, offset_ctx)
            tile.rect = original
        surface.set_clip(clip)

        if self._max_scroll > 0:
            track_x = self._tiles_rect.right - ctx.u(3)
            frac = self._tiles_rect.height / self._content_height
            bar_h = max(ctx.u(24), self._tiles_rect.height * frac)
            travel = (self._tiles_rect.height - bar_h) * (self.scroll / self._max_scroll)
            pygame.draw.rect(surface, t.rule_bright,
                             pygame.Rect(track_x, round(self._tiles_rect.top + travel),
                                         ctx.px(3), round(bar_h)))
        self._draw_pager(surface, ctx)

        self._draw_diagnostics(surface, ctx)

    def _draw_pager(self, surface: pygame.Surface, ctx: UIContext) -> None:
        if not self.btn_page_next.visible:
            return
        t = ctx.theme
        # the wheel can leave the offset between pages, so the buttons take
        # their enabled state from the offset itself, not from the page number
        self.btn_page_prev.enabled = self.scroll > 0
        self.btn_page_next.enabled = self.scroll < self._max_scroll
        self.btn_page_prev.draw(surface, ctx)
        self.btn_page_next.draw(surface, ctx)
        blit_text(surface, ctx.book, f"PAGE {self.page + 1}/{self.pages}",
                  ctx.font_px(t.size_micro), t.inop,
                  (self._list_rect.left, self.btn_page_prev.rect.centery),
                  anchor="midleft", mono=True)

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
