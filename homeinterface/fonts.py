"""Font resolution and caching.

Fonts are rasterised at the exact pixel size demanded by the current
viewport, so text stays crisp at any resolution instead of being scaled from
a fixed-size surface.  Rendered glyph surfaces are cached per
(family, size, bold, text, colour) because pygame's ``Font.render`` is the
hot path in a panel full of static labels.
"""

from __future__ import annotations

from typing import Iterable

import pygame

from .theme import RGB, Theme

_CACHE_LIMIT = 4096


class FontBook:
    """Resolves the theme's font stacks to real system fonts, once."""

    def __init__(self, theme: Theme):
        self.theme = theme
        self._ui_path = self._resolve(theme.font_stack)
        self._mono_path = self._resolve(theme.mono_stack)
        self._fonts: dict[tuple[str, int, bool], pygame.font.Font] = {}
        self._surfaces: dict[tuple, pygame.Surface] = {}

    @staticmethod
    def _resolve(candidates: Iterable[str]) -> str | None:
        for name in candidates:
            path = pygame.font.match_font(name)
            if path:
                return path
        return None

    def font(self, size_px: int, *, mono: bool = False, bold: bool = False) -> pygame.font.Font:
        path = self._mono_path if mono else self._ui_path
        key = (path or "", size_px, bold)
        font = self._fonts.get(key)
        if font is None:
            font = pygame.font.Font(path, size_px) if path else pygame.font.Font(None, size_px)
            font.set_bold(bold)
            self._fonts[key] = font
        return font

    def render(
        self,
        text: str,
        size_px: int,
        color: RGB,
        *,
        mono: bool = False,
        bold: bool = False,
    ) -> pygame.Surface:
        key = (text, size_px, color, mono, bold)
        surface = self._surfaces.get(key)
        if surface is None:
            if len(self._surfaces) > _CACHE_LIMIT:
                self._surfaces.clear()
            surface = self.font(size_px, mono=mono, bold=bold).render(text, True, color)
            self._surfaces[key] = surface
        return surface

    def clear_raster_cache(self) -> None:
        """Call on resize: every cached surface is at the old pixel size."""
        self._fonts.clear()
        self._surfaces.clear()


ANCHORS = {
    "topleft", "midtop", "topright",
    "midleft", "center", "midright",
    "bottomleft", "midbottom", "bottomright",
}


def blit_text(
    surface: pygame.Surface,
    book: FontBook,
    text: str,
    size_px: int,
    color: RGB,
    pos: tuple[float, float],
    *,
    anchor: str = "topleft",
    mono: bool = False,
    bold: bool = False,
) -> pygame.Rect:
    """Draw ``text`` with ``pos`` interpreted as the named anchor point."""
    if anchor not in ANCHORS:
        raise ValueError(f"unknown anchor {anchor!r}")
    glyphs = book.render(text, size_px, color, mono=mono, bold=bold)
    rect = glyphs.get_rect(**{anchor: (round(pos[0]), round(pos[1]))})
    surface.blit(glyphs, rect)
    return rect


def truncate(book: FontBook, text: str, size_px: int, max_width: float, *, mono: bool = False) -> str:
    """Shorten with an ellipsis until it fits ``max_width`` pixels."""
    font = book.font(size_px, mono=mono)
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size(text[:mid] + ellipsis)[0] <= max_width:
            lo = mid
        else:
            hi = mid - 1
    # never return nothing: an empty label reads as a broken widget, whereas
    # a single clipped character still tells the operator something is there
    return text[:lo] + ellipsis if lo else text[:1]
