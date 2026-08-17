#!/usr/bin/env python3
"""Touch calibration for an SPI panel: tap four targets, get a config block.

A resistive digitiser reports raw ADC counts in whatever orientation it was
glued down in, and the framebuffer's ``rotate=`` parameter does not move them.
So the panel can be perfectly readable while taps land on the wrong side of
the screen.  This tool draws a target at each corner, watches which raw axis
moves, and prints the ``display.touch_calibration`` block that lines the two
up - including extrapolated axis ranges, because a resistive panel's corners
rarely reach the driver's nominal 0..4095.

Run it on the Pi, on the panel itself:

    python tools/touchcal.py                 # calibrate
    python tools/touchcal.py --raw           # just dump raw events

Ctrl-C aborts. Nothing is written; copy the printed block into config/app.yaml.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402  (must follow the SDL env var above)

from homeinterface import fbdev  # noqa: E402

#: fractional positions of the four targets, in tap order
TARGETS = (("top-left", 0.1, 0.1), ("top-right", 0.9, 0.1),
           ("bottom-right", 0.9, 0.9), ("bottom-left", 0.1, 0.9))
INSET = 0.1  # how far the targets sit from the edges, for extrapolation


def draw_target(surface: pygame.Surface, font: pygame.font.Font, point: tuple[int, int],
                caption: str) -> None:
    surface.fill((7, 10, 14))
    x, y = point
    arm, colour = 14, (0, 214, 120)
    pygame.draw.line(surface, colour, (x - arm, y), (x + arm, y), 2)
    pygame.draw.line(surface, colour, (x, y - arm), (x, y + arm), 2)
    pygame.draw.circle(surface, colour, point, arm - 4, 1)
    for i, line in enumerate(("TOUCH CALIBRATION", caption)):
        text = font.render(line, True, (255, 255, 255) if i else (95, 199, 245))
        surface.blit(text, (surface.get_width() // 2 - text.get_width() // 2,
                            surface.get_height() // 2 - 20 + i * 18))


def collect(fb: fbdev.Framebuffer, touch: fbdev.TouchPanel) -> list[tuple[int, int]]:
    """Show each target in turn and return the raw counts tapped at each."""
    surface = fb.new_surface()
    font = pygame.font.Font(None, 18)
    width, height = fb.size
    raws: list[tuple[int, int]] = []
    for name, fx, fy in TARGETS:
        point = (round(fx * (width - 1)), round(fy * (height - 1)))
        draw_target(surface, font, point, f"tap the {name} target")
        fb.present(surface)
        print(f"  tap {name} ...", end="", flush=True)
        raws.append(wait_for_tap(touch))
        print(f" raw={raws[-1]}")
        time.sleep(0.25)  # let the finger leave before the next target arms
        touch.pump()
    return raws


def wait_for_tap(touch: fbdev.TouchPanel) -> tuple[int, int]:
    """Block until one press-release cycle completes; return its raw counts."""
    was_down = False
    while True:
        pygame.event.clear()
        touch.pump()
        if touch.pressed:
            was_down = True
            captured = tuple(touch._raw)  # noqa: SLF001 - raw counts are the point
        elif was_down:
            return captured  # type: ignore[return-value]
        time.sleep(0.01)


def solve(raws: list[tuple[int, int]]) -> fbdev.TouchCalibration:
    """Infer swap/invert and the full-screen axis ranges from four corner taps."""
    tl, tr, br, bl = raws
    # which raw axis tracks screen X? the one that moves along the top edge
    dx_along_x = abs(tr[0] - tl[0]) + abs(br[0] - bl[0])
    dy_along_x = abs(tr[1] - tl[1]) + abs(br[1] - bl[1])
    swap = dy_along_x > dx_along_x

    # raw index that follows screen X, and the one that follows screen Y
    ix, iy = (1, 0) if swap else (0, 1)
    left = (tl[ix] + bl[ix]) / 2
    right = (tr[ix] + br[ix]) / 2
    top = (tl[iy] + tr[iy]) / 2
    bottom = (bl[iy] + br[iy]) / 2

    x_span, invert_x = extrapolate(left, right)
    y_span, invert_y = extrapolate(top, bottom)
    # the ranges are indexed by *raw* axis, so a swapped panel stores them the
    # other way round - TouchCalibration normalises before it swaps
    spans = {("x" if not swap else "y") + "_range": x_span,
             ("y" if not swap else "x") + "_range": y_span}
    return fbdev.TouchCalibration(swap_xy=swap, invert_x=invert_x, invert_y=invert_y, **spans)


def extrapolate(low_edge: float, high_edge: float) -> tuple[tuple[int, int], bool]:
    """Project the two inset taps out to the panel edges.

    Returns an ascending ``(min, max)`` range plus whether the raw axis runs
    backwards relative to the screen, which is what the invert flag means.
    """
    step = (high_edge - low_edge) / (1.0 - 2 * INSET)
    at_zero = low_edge - step * INSET
    at_one = high_edge + step * INSET
    inverted = at_one < at_zero
    lo, hi = sorted((round(at_zero), round(at_one)))
    return (lo, max(hi, lo + 1)), inverted


def dump_raw(touch: fbdev.TouchPanel) -> None:
    print(f"raw events from {touch.path} ({touch.name}) - Ctrl-C to stop")
    print(f"driver axis ranges: x={touch.calibration.x_range} y={touch.calibration.y_range}")
    last = None
    while True:
        touch.pump()
        state = (tuple(touch._raw), touch.pressed)  # noqa: SLF001
        if state != last:
            raw, down = state
            print(f"  raw={raw}  {'DOWN' if down else 'up  '}  mapped={touch.pos}")
            last = state
        pygame.event.clear()
        time.sleep(0.02)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fbdev", help="framebuffer device (default: probe /dev/fb1, /dev/fb0)")
    parser.add_argument("--touch", help="touch device (default: autodetect)")
    parser.add_argument("--raw", action="store_true", help="dump raw events instead of calibrating")
    args = parser.parse_args(argv)

    pygame.init()
    pygame.font.init()
    device = fbdev.default_device(args.fbdev)
    if device is None:
        print("no framebuffer device found - is the fbtft overlay loaded?", file=sys.stderr)
        return 1
    touch_path = args.touch or fbdev.find_touch_device()
    if not touch_path:
        print("no touch device found under /dev/input/event*", file=sys.stderr)
        return 1

    fb = fbdev.Framebuffer(device)
    touch = fbdev.TouchPanel(touch_path, fb.size)
    try:
        if args.raw:
            dump_raw(touch)
            return 0
        print(f"panel {device} {fb.size[0]}x{fb.size[1]}, touch {touch_path} ({touch.name})")
        cal = solve(collect(fb, touch))
        print("\nAdd this to config/app.yaml under display:\n")
        print("  touch_calibration:")
        print(f"    swap_xy: {str(cal.swap_xy).lower()}")
        print(f"    invert_x: {str(cal.invert_x).lower()}")
        print(f"    invert_y: {str(cal.invert_y).lower()}")
        print(f"    x_range: [{cal.x_range[0]}, {cal.x_range[1]}]")
        print(f"    y_range: [{cal.y_range[0]}, {cal.y_range[1]}]")
        return 0
    except KeyboardInterrupt:
        print("\naborted")
        return 130
    finally:
        touch.close()
        fb.blank()
        fb.close()
        pygame.quit()


if __name__ == "__main__":
    raise SystemExit(main())
