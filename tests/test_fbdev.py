"""SPI-panel output and touch mapping.

None of this needs a Raspberry Pi: :class:`FrameWriter` pushes into a plain
bytearray, and the touch maths is pure.  The parts that genuinely need the
kernel (ioctl probing, evdev reads) are not covered here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pygame
import pytest

from homeinterface.fbdev import (FbGeometry, FrameWriter, PanelError, TouchCalibration,
                                 default_device, rgb565_surface)

PANEL = FbGeometry(width=480, height=320, bits_per_pixel=16, line_length=960)


def _writer(geometry: FbGeometry = PANEL) -> tuple[FrameWriter, bytearray]:
    target = bytearray(geometry.line_length * geometry.height)
    return FrameWriter(geometry, target), target


def test_rgb565_surface_matches_panel_format():
    surface = rgb565_surface((480, 320))
    assert surface.get_bitsize() == 16
    assert surface.get_masks()[:3] == (0xF800, 0x07E0, 0x001F)
    # two bytes per pixel is the whole point: no conversion pass on present()
    assert surface.get_bytesize() == 2


def test_first_frame_is_written_whole_then_unchanged_frames_cost_nothing():
    writer, target = _writer()
    surface = writer.new_surface()
    surface.fill((0, 214, 120))

    assert writer.present(surface) == PANEL.row_bytes * PANEL.height
    assert target[:2] != b"\0\0"
    assert writer.present(surface) == 0


def test_only_changed_rows_are_pushed():
    writer, _ = _writer()
    surface = writer.new_surface()
    surface.fill((0, 0, 0))
    writer.present(surface)

    pygame.draw.rect(surface, (255, 66, 56), pygame.Rect(0, 100, 480, 10))
    assert writer.present(surface) == PANEL.row_bytes * 10


def test_dirty_rows_may_be_discontiguous():
    writer, _ = _writer()
    surface = writer.new_surface()
    surface.fill((0, 0, 0))
    writer.present(surface)

    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(0, 0, 480, 2))
    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(0, 300, 480, 3))
    assert writer.present(surface) == PANEL.row_bytes * 5


def test_padded_stride_leaves_the_pad_untouched():
    """A panel whose line_length exceeds the visible row must not be smeared."""
    geometry = FbGeometry(width=8, height=4, bits_per_pixel=16, line_length=32)
    writer, target = _writer(geometry)
    surface = writer.new_surface()
    surface.fill((255, 255, 255))

    assert writer.present(surface) == 16 * 4
    for y in range(geometry.height):
        row = target[y * 32:(y + 1) * 32]
        assert row[:16] != bytes(16)
        assert row[16:] == bytes(16)  # padding stays as the driver left it


def test_blank_clears_panel_and_shadow():
    writer, target = _writer()
    surface = writer.new_surface()
    surface.fill((0, 214, 120))
    writer.present(surface)

    writer.blank()
    assert target == bytearray(len(target))
    # the shadow was cleared too, so the same frame is dirty again
    assert writer.present(surface) == PANEL.row_bytes * PANEL.height


def test_present_rejects_mismatched_surfaces():
    writer, _ = _writer()
    with pytest.raises(PanelError):
        writer.present(rgb565_surface((320, 240)))
    with pytest.raises(PanelError):
        writer.present(pygame.Surface(PANEL.size, 0, 32))


def test_default_device_refuses_a_missing_explicit_request(tmp_path):
    assert default_device(str(tmp_path / "nope")) is None
    existing = tmp_path / "fbX"
    existing.write_bytes(b"")
    assert default_device(str(existing)) == str(existing)


# -- touch ---------------------------------------------------------------

SIZE = (480, 320)


def test_identity_calibration_maps_corners_to_corners():
    cal = TouchCalibration(x_range=(0, 4095), y_range=(0, 4095))
    assert cal.map(0, 0, SIZE) == (0, 0)
    assert cal.map(4095, 4095, SIZE) == (479, 319)
    assert cal.map(2048, 2048, SIZE) == (240, 160)


def test_out_of_range_counts_clamp_into_the_panel():
    cal = TouchCalibration(x_range=(200, 3800), y_range=(200, 3800))
    assert cal.map(-500, 9000, SIZE) == (0, 319)


def test_swap_happens_before_invert():
    cal = TouchCalibration(x_range=(0, 999), y_range=(0, 999), swap_xy=True, invert_y=True)
    # raw x drives screen Y (swapped), then Y is flipped
    assert cal.map(0, 999, SIZE) == (479, 319)
    assert cal.map(999, 0, SIZE) == (0, 0)


def test_from_dict_reads_flags_and_ranges():
    cal = TouchCalibration.from_dict(
        {"swap_xy": True, "invert_x": True, "x_range": [150, 3900], "y_range": (100, 4000)}
    )
    assert (cal.swap_xy, cal.invert_x, cal.invert_y) == (True, True, False)
    assert cal.x_range == (150, 3900)
    assert cal.y_range == (100, 4000)


def test_from_dict_ignores_a_malformed_range():
    cal = TouchCalibration.from_dict({"x_range": [7]})
    assert cal.x_range == TouchCalibration().x_range


# -- the calibration tool ------------------------------------------------

def _load_touchcal():
    path = Path(__file__).resolve().parent.parent / "tools" / "touchcal.py"
    spec = importlib.util.spec_from_file_location("touchcal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _simulate(cal: TouchCalibration, fx: float, fy: float) -> tuple[int, int]:
    """Raw counts a digitiser with calibration ``cal`` would report at (fx, fy).

    The inverse of :meth:`TouchCalibration.map`, in fractions rather than
    pixels, so a solved calibration can be checked against the one that
    generated the taps.
    """
    if cal.invert_x:
        fx = 1.0 - fx
    if cal.invert_y:
        fy = 1.0 - fy
    if cal.swap_xy:
        fx, fy = fy, fx
    def count(fraction, span):
        return round(span[0] + fraction * (span[1] - span[0]))
    return count(fx, cal.x_range), count(fy, cal.y_range)


@pytest.mark.parametrize("truth", [
    TouchCalibration(x_range=(200, 3900), y_range=(150, 3950)),
    TouchCalibration(x_range=(200, 3900), y_range=(150, 3950), swap_xy=True),
    TouchCalibration(x_range=(200, 3900), y_range=(150, 3950), swap_xy=True, invert_y=True),
    TouchCalibration(x_range=(300, 3700), y_range=(300, 3700), invert_x=True, invert_y=True),
])
def test_solve_recovers_the_orientation_it_was_taught(truth):
    touchcal = _load_touchcal()
    taps = [_simulate(truth, fx, fy) for _name, fx, fy in touchcal.TARGETS]

    solved = touchcal.solve(taps)
    assert solved.swap_xy == truth.swap_xy
    assert solved.invert_x == truth.invert_x
    assert solved.invert_y == truth.invert_y
    # extrapolating from taps 10% inside the edges should land near the real
    # range; a few counts of rounding error is fine on a 4096-count axis
    for solved_span, true_span in ((solved.x_range, truth.x_range),
                                   (solved.y_range, truth.y_range)):
        assert abs(solved_span[0] - true_span[0]) <= 3
        assert abs(solved_span[1] - true_span[1]) <= 3


def test_solved_calibration_round_trips_to_the_tapped_points():
    touchcal = _load_touchcal()
    truth = TouchCalibration(x_range=(180, 3880), y_range=(220, 3820),
                             swap_xy=True, invert_x=True)
    solved = touchcal.solve([_simulate(truth, fx, fy) for _n, fx, fy in touchcal.TARGETS])

    for _name, fx, fy in touchcal.TARGETS:
        expected = (round(fx * (SIZE[0] - 1)), round(fy * (SIZE[1] - 1)))
        got = solved.map(*_simulate(truth, fx, fy), SIZE)
        assert abs(got[0] - expected[0]) <= 2
        assert abs(got[1] - expected[1]) <= 2


# -- end to end ----------------------------------------------------------

def test_every_screen_draws_into_a_panel_surface_and_pushes_rows():
    """The whole UI must survive a 16bpp target, not just a 32bpp window.

    Nothing in draw.py may assume a 32bpp destination: a panel frame is
    RGB565, and a blit that silently fails there would only show up on the
    hardware.
    """
    from homeinterface.app import App
    from homeinterface.config import load_config

    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "app.yaml")
    config["display"].update(width=480, height=320, driver="window", fullscreen=False)
    config["backend"] = {"kind": "mock", "chaos": False}
    app = App(config)
    app._open_window()
    app._build_screens()
    app.surface = rgb565_surface((480, 320))
    app._on_resize(app.surface.get_size())

    writer, target = _writer()
    pushed = []
    for index in range(len(app.screens)):
        app.show(index)
        ctx = app._context()
        title_r, rail_r, content_r, footer_r = app._regions(ctx)
        app._layout_chrome(rail_r, title_r, footer_r, ctx)
        app.screen.ensure_layout(content_r, ctx)
        app._draw(ctx, title_r, rail_r, content_r, footer_r)
        pushed.append(writer.present(app.surface))

    assert pushed[0] == PANEL.row_bytes * PANEL.height  # first frame is whole
    # each screen differs from the previous one, but none is a full repaint of
    # every row - the chrome is identical between pages
    assert all(0 < n < PANEL.row_bytes * PANEL.height for n in pushed[1:])
    assert target != bytearray(len(target))
