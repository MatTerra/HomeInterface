"""Vector drawing primitives.

Every shape is generated from geometry at the current pixel size - there are
no bitmaps and no pre-rendered assets anywhere in the UI, which is what makes
the panel scale to arbitrary resolutions without softening.

The house style is angular: chamfered corners, corner brackets, tick scales.
Rounded rectangles are deliberately absent.
"""

from __future__ import annotations

import math

import pygame

from .theme import RGB

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# chamfered (cut-corner) rectangles - the base shape of every panel and button
# ---------------------------------------------------------------------------

def chamfer_points(rect: pygame.Rect, cut: float, corners: str = "tlbr") -> list[Point]:
    """Polygon for ``rect`` with the named corners cut at 45 degrees.

    ``corners`` is any subset of ``"tl"``, ``"tr"``, ``"br"``, ``"bl"``
    concatenated, e.g. ``"tlbr"`` for the diagonal pair.
    """
    cut = max(0.0, min(cut, rect.width / 2, rect.height / 2))
    l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
    tl = "tl" in corners
    tr = "tr" in corners
    br = "br" in corners
    bl = "bl" in corners
    pts: list[Point] = []
    pts.append((l + cut, t) if tl else (l, t))
    pts.append((r - cut, t) if tr else (r, t))
    if tr:
        pts.append((r, t + cut))
    pts.append((r, b - cut) if br else (r, b))
    if br:
        pts.append((r - cut, b))
    pts.append((l + cut, b) if bl else (l, b))
    if bl:
        pts.append((l, b - cut))
    pts.append((l, t + cut) if tl else (l, t))
    # collapse duplicates introduced when a corner is not cut
    out: list[Point] = []
    for p in pts:
        if not out or p != out[-1]:
            out.append(p)
    return out


def chamfer_rect(
    surface: pygame.Surface,
    rect: pygame.Rect,
    *,
    fill: RGB | None = None,
    outline: RGB | None = None,
    width: int = 1,
    cut: float = 0.0,
    corners: str = "tlbr",
    alpha: int = 255,
) -> None:
    pts = chamfer_points(rect, cut, corners)
    if fill is not None:
        if alpha >= 255:
            pygame.draw.polygon(surface, fill, pts)
        else:
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            local = [(x - rect.left, y - rect.top) for x, y in pts]
            pygame.draw.polygon(layer, (*fill, alpha), local)
            surface.blit(layer, rect.topleft)
    if outline is not None and width > 0:
        pygame.draw.polygon(surface, outline, pts, width)


def bracket_frame(
    surface: pygame.Surface,
    rect: pygame.Rect,
    color: RGB,
    *,
    width: int = 2,
    arm: float = 18.0,
) -> None:
    """Four corner brackets - the selection/focus cue on avionics displays."""
    arm = min(arm, rect.width / 2.5, rect.height / 2.5)
    l, t, r, b = rect.left, rect.top, rect.right - 1, rect.bottom - 1
    segments = [
        ((l, t + arm), (l, t), (l + arm, t)),
        ((r - arm, t), (r, t), (r, t + arm)),
        ((r, b - arm), (r, b), (r - arm, b)),
        ((l + arm, b), (l, b), (l, b - arm)),
    ]
    for seg in segments:
        pygame.draw.lines(surface, color, False, seg, width)


# ---------------------------------------------------------------------------
# lines and scales
# ---------------------------------------------------------------------------

def dashed_line(
    surface: pygame.Surface,
    color: RGB,
    start: Point,
    end: Point,
    *,
    width: int = 1,
    dash: float = 8.0,
    gap: float = 6.0,
) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return
    ux, uy = dx / length, dy / length
    step = dash + gap
    travelled = 0.0
    while travelled < length:
        seg = min(dash, length - travelled)
        a = (start[0] + ux * travelled, start[1] + uy * travelled)
        b = (start[0] + ux * (travelled + seg), start[1] + uy * (travelled + seg))
        pygame.draw.line(surface, color, a, b, width)
        travelled += step


def tick_scale(
    surface: pygame.Surface,
    color: RGB,
    origin: Point,
    length: float,
    *,
    count: int,
    tick: float,
    width: int = 1,
    vertical: bool = False,
    major_every: int = 5,
    major_scale: float = 1.8,
) -> None:
    """A linear tick strip, used along gauges and timeline strips."""
    if count < 2:
        return
    step = length / (count - 1)
    for i in range(count):
        size = tick * (major_scale if i % major_every == 0 else 1.0)
        if vertical:
            y = origin[1] + i * step
            pygame.draw.line(surface, color, (origin[0], y), (origin[0] + size, y), width)
        else:
            x = origin[0] + i * step
            pygame.draw.line(surface, color, (x, origin[1]), (x, origin[1] + size), width)


def arc(
    surface: pygame.Surface,
    color: RGB,
    center: Point,
    radius: float,
    start_deg: float,
    end_deg: float,
    *,
    width: int = 2,
    steps: int | None = None,
) -> None:
    """Polyline arc - smoother and thicker-capable than ``pygame.draw.arc``.

    Angles are in degrees, clockwise, 0 = straight up (12 o'clock).
    """
    sweep = end_deg - start_deg
    if abs(sweep) < 0.01 or radius <= 0:
        return
    if steps is None:
        steps = max(6, int(abs(sweep) * radius / 220) + 6)
    pts = []
    for i in range(steps + 1):
        a = math.radians(start_deg + sweep * i / steps - 90.0)
        pts.append((center[0] + math.cos(a) * radius, center[1] + math.sin(a) * radius))
    pygame.draw.lines(surface, color, False, pts, width)


def radial_point(center: Point, radius: float, angle_deg: float) -> Point:
    a = math.radians(angle_deg - 90.0)
    return (center[0] + math.cos(a) * radius, center[1] + math.sin(a) * radius)


def pointer(
    surface: pygame.Surface,
    color: RGB,
    center: Point,
    angle_deg: float,
    *,
    inner: float,
    outer: float,
    width: float,
) -> None:
    """Tapered needle, drawn as a triangle so it thins toward the tip."""
    tip = radial_point(center, outer, angle_deg)
    base = radial_point(center, inner, angle_deg)
    perp = angle_deg + 90.0
    half = width / 2.0
    a = radial_point(base, half, perp)
    b = radial_point(base, -half, perp)
    pygame.draw.polygon(surface, color, [tip, a, b])


# ---------------------------------------------------------------------------
# fills
# ---------------------------------------------------------------------------

_HAIRLINE_GRID_CACHE: dict[tuple, pygame.Surface] = {}
_HAIRLINE_GRID_CACHE_LIMIT = 32


def clear_caches() -> None:
    """Drop all cached rendering layers (call on resize)."""
    _HAIRLINE_GRID_CACHE.clear()


def hairline_grid(
    surface: pygame.Surface,
    rect: pygame.Rect,
    color: RGB,
    *,
    spacing: float,
    alpha: int = 40,
    width: int = 1,
) -> None:
    """Faint background lattice, the 'engineering paper' texture."""
    if spacing <= 1:
        return
    key = (rect.width, rect.height, color, round(spacing, 2), alpha, width)
    layer = _HAIRLINE_GRID_CACHE.get(key)
    if layer is None:
        if len(_HAIRLINE_GRID_CACHE) >= _HAIRLINE_GRID_CACHE_LIMIT:
            _HAIRLINE_GRID_CACHE.clear()
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        c = (*color, alpha)
        x = 0.0
        while x < rect.width:
            pygame.draw.line(layer, c, (x, 0), (x, rect.height), width)
            x += spacing
        y = 0.0
        while y < rect.height:
            pygame.draw.line(layer, c, (0, y), (rect.width, y), width)
            y += spacing
        _HAIRLINE_GRID_CACHE[key] = layer
    surface.blit(layer, rect.topleft)


def translucent_polygon(
    surface: pygame.Surface,
    points: list[Point],
    color: RGB,
    alpha: int,
) -> None:
    if len(points) < 3:
        return
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    left, top = math.floor(min(xs)), math.floor(min(ys))
    w = max(1, math.ceil(max(xs)) - left)
    h = max(1, math.ceil(max(ys)) - top)
    layer = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.polygon(layer, (*color, alpha), [(x - left, y - top) for x, y in points])
    surface.blit(layer, (left, top))
