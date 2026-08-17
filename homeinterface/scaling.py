"""Resolution-independent layout.

Everything in the UI is expressed either in *design units* (a 480x320
reference canvas) or in *fractions* of a parent rectangle.  Nothing is ever
written in raw pixels, so the same code renders identically on the 480x320
SPI panel this is tuned for and on an 8K wall panel.

The reference is the *smallest* supported panel on purpose.  Design units
therefore equal pixels on the target hardware, which is where legibility and
touch-target sizes actually have to be verified; bigger displays get a scale
factor above 1.0, and scaling type and strokes *up* is safe in a way that
scaling them down past the font rasteriser's floor is not.

Two conversions live here:

* ``Viewport.u(v)``   design units -> pixels, uniform scale, for stroke
  widths, corner cuts, paddings and font sizes.
* ``Box``             fractional sub-rectangles of a parent, for layout.

Design units are deliberately *not* used for positioning: on a 21:9 panel a
1920x1080 canvas would letterbox and waste a third of the screen.  Layout is
fluid (fractions of the real surface), only the "ink" scales uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

#: the 480x320 SPI TFT this interface is tuned for
REF_WIDTH = 480.0
REF_HEIGHT = 320.0


@dataclass(frozen=True)
class Viewport:
    """Maps design units to pixels for one surface size."""

    width: int
    height: int
    ref_width: float = REF_WIDTH
    ref_height: float = REF_HEIGHT
    #: extra multiplier, e.g. to bump everything up on a touch panel
    density: float = 1.0

    @property
    def scale(self) -> float:
        return min(self.width / self.ref_width, self.height / self.ref_height) * self.density

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 1.0

    @property
    def design_width(self) -> float:
        """Width in design units - how much *room* there is, not how many px.

        Use this to decide whether a label fits: a 4K panel has enormous
        pixel width but the same design width as the reference if the ink
        scaled with it.
        """
        return self.width / self.scale

    @property
    def design_height(self) -> float:
        return self.height / self.scale

    @property
    def landscape(self) -> bool:
        """Wide enough to put a side panel next to the main content.

        True at the 480x320 baseline (1.5), so the plan screen keeps its
        inspector beside the drawing rather than stacking it underneath -
        320px of height cannot afford a stacked panel.
        """
        return self.aspect >= 1.35

    @property
    def is_wide(self) -> bool:
        """True for 21:9 and wider - room for a third column."""
        return self.aspect >= 2.0

    @property
    def compact(self) -> bool:
        """True on panels below the 480x320 reference.

        Font rasterisation has an 8px floor while boxes keep shrinking
        linearly, so under the reference size labels start colliding.
        Screens use this to shed secondary text (subtitles, dates,
        sub-labels) rather than render an unreadable pile.
        """
        return self.scale < 0.95

    def u(self, v: float) -> float:
        """Design units -> pixels (float, for anti-aliased geometry)."""
        return v * self.scale

    def px(self, v: float, minimum: int = 1) -> int:
        """Design units -> whole pixels, never collapsing to zero."""
        return max(minimum, round(v * self.scale))

    def font_px(self, v: float) -> int:
        return max(8, round(v * self.scale))

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(0, 0, self.width, self.height)

    def resized(self, width: int, height: int) -> "Viewport":
        return Viewport(width, height, self.ref_width, self.ref_height, self.density)


class Box:
    """Fractional layout helper over a pygame.Rect.

    All methods return new ``Box`` instances, so layouts read as a chain::

        header, body = Box(area).rows(0.1, 0.9)
        rail, content = body.cols(0.16, 0.84)
        panel = content.pad(vp.u(12)).rect
    """

    __slots__ = ("rect",)

    def __init__(self, rect: pygame.Rect):
        self.rect = pygame.Rect(rect)

    # -- construction ----------------------------------------------------
    @classmethod
    def of(cls, x: float, y: float, w: float, h: float) -> "Box":
        return cls(pygame.Rect(round(x), round(y), round(w), round(h)))

    # -- subdivision -----------------------------------------------------
    def cols(self, *weights: float, gap: float = 0.0) -> list["Box"]:
        return self._split(weights, gap, horizontal=True)

    def rows(self, *weights: float, gap: float = 0.0) -> list["Box"]:
        return self._split(weights, gap, horizontal=False)

    def _split(self, weights: tuple[float, ...], gap: float, horizontal: bool) -> list["Box"]:
        total = sum(weights) or 1.0
        span = (self.rect.width if horizontal else self.rect.height) - gap * (len(weights) - 1)
        out: list[Box] = []
        cursor = float(self.rect.left if horizontal else self.rect.top)
        for w in weights:
            size = span * (w / total)
            if horizontal:
                out.append(Box.of(cursor, self.rect.top, size, self.rect.height))
            else:
                out.append(Box.of(self.rect.left, cursor, self.rect.width, size))
            cursor += size + gap
        return out

    def grid(self, cols: int, rows: int, gap: float = 0.0) -> list["Box"]:
        """Row-major grid of equal cells."""
        cells: list[Box] = []
        cw = (self.rect.width - gap * (cols - 1)) / cols
        ch = (self.rect.height - gap * (rows - 1)) / rows
        for r in range(rows):
            for c in range(cols):
                cells.append(
                    Box.of(
                        self.rect.left + c * (cw + gap),
                        self.rect.top + r * (ch + gap),
                        cw,
                        ch,
                    )
                )
        return cells

    # -- adjustment ------------------------------------------------------
    def pad(self, amount: float, y: float | None = None) -> "Box":
        dx = round(amount)
        dy = round(amount if y is None else y)
        return Box(self.rect.inflate(-2 * dx, -2 * dy))

    def inset(self, left: float = 0, top: float = 0, right: float = 0, bottom: float = 0) -> "Box":
        return Box.of(
            self.rect.left + left,
            self.rect.top + top,
            self.rect.width - left - right,
            self.rect.height - top - bottom,
        )

    def top_slice(self, height: float) -> "Box":
        return Box.of(self.rect.left, self.rect.top, self.rect.width, height)

    def bottom_slice(self, height: float) -> "Box":
        return Box.of(self.rect.left, self.rect.bottom - height, self.rect.width, height)

    def fit(self, aspect: float) -> "Box":
        """Largest centred sub-box with the given width/height ratio."""
        w, h = self.rect.width, self.rect.height
        if w / h > aspect:
            w = h * aspect
        else:
            h = w / aspect
        return Box.of(
            self.rect.centerx - w / 2, self.rect.centery - h / 2, w, h
        )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Box({self.rect.left},{self.rect.top},{self.rect.width},{self.rect.height})"
