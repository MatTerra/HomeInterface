from __future__ import annotations

import pytest

from homeinterface.scaling import REF_HEIGHT, REF_WIDTH, Box, Viewport


@pytest.mark.parametrize(
    "width,height",
    [
        (1920, 1080),
        (2560, 1080),
        (3840, 2160),
        (1280, 800),
    ],
)
def test_viewport_scale_and_helpers_never_collapse(width, height):
    vp = Viewport(width, height)
    assert vp.scale > 0
    # u() should track the uniform scale for a representative design value
    assert vp.u(10.0) == pytest.approx(10.0 * vp.scale)
    # px()/font_px() never round down to zero, even for a tiny design value
    assert vp.px(0.0001) >= 1
    assert vp.font_px(0.0001) >= 8


def test_viewport_scale_is_min_of_axes():
    # 480x320 is exactly the reference canvas -> scale 1, so design units
    # equal pixels on the target panel
    vp = Viewport(REF_WIDTH, REF_HEIGHT)
    assert vp.scale == pytest.approx(1.0)

    # 960x640 is exactly a 2x scale of the reference canvas
    vp = Viewport(2 * REF_WIDTH, 2 * REF_HEIGHT)
    assert vp.scale == pytest.approx(2.0)

    # 2560x1080 is wider than 3:2 so height is the limiting axis
    vp = Viewport(2560, 1080)
    assert vp.scale == pytest.approx(1080 / REF_HEIGHT)

    # a 16:10 panel is limited by whichever axis is tightest
    vp = Viewport(1280, 800)
    assert vp.scale == pytest.approx(min(1280 / REF_WIDTH, 800 / REF_HEIGHT))


def test_reference_canvas_is_the_smallest_supported_panel():
    # the baseline is deliberately the small SPI TFT: every other display
    # scales up from it, never down past the font rasteriser's floor
    assert (REF_WIDTH, REF_HEIGHT) == (480.0, 320.0)
    assert Viewport(480, 320).compact is False


def test_viewport_density_multiplies_scale():
    vp = Viewport(REF_WIDTH, REF_HEIGHT, density=1.5)
    assert vp.scale == pytest.approx(1.5)


def test_is_wide_true_for_21_9_false_for_16_9():
    assert Viewport(2560, 1080).is_wide is True  # ~2.37:1
    assert Viewport(1920, 1080).is_wide is False  # 16:9
    assert Viewport(3840, 2160).is_wide is False  # 16:9


def test_viewport_resized_preserves_ref_and_density():
    vp = Viewport(1920, 1080, density=1.2)
    resized = vp.resized(1280, 800)
    assert resized.width == 1280
    assert resized.height == 800
    assert resized.ref_width == vp.ref_width
    assert resized.ref_height == vp.ref_height
    assert resized.density == vp.density


# -- Box ----------------------------------------------------------------


def test_box_cols_weights_and_gap_sum_to_parent_span():
    box = Box.of(0, 0, 1000, 500)
    gap = 10
    cols = box.cols(1, 2, 1, gap=gap)
    assert len(cols) == 3
    total_width = sum(c.rect.width for c in cols) + gap * 2
    assert abs(total_width - box.rect.width) <= 3  # rounding slack
    # weighted: the middle column should be roughly twice as wide as the sides
    assert cols[1].rect.width == pytest.approx(cols[0].rect.width * 2, abs=3)
    assert cols[0].rect.width == pytest.approx(cols[2].rect.width, abs=3)


def test_box_rows_weights_and_gap_sum_to_parent_span():
    box = Box.of(0, 0, 500, 900)
    gap = 8
    rows = box.rows(1, 1, 2, gap=gap)
    total_height = sum(r.rect.height for r in rows) + gap * 2
    assert abs(total_height - box.rect.height) <= 3


def test_box_grid_cell_count_and_coverage():
    box = Box.of(0, 0, 600, 400)
    cells = box.grid(3, 2, gap=4)
    assert len(cells) == 6
    # row-major order: first row's cells share the same top
    assert cells[0].rect.top == cells[1].rect.top == cells[2].rect.top
    assert cells[3].rect.top == cells[4].rect.top == cells[5].rect.top
    assert cells[3].rect.top > cells[0].rect.top


def test_box_fit_preserves_requested_aspect():
    box = Box.of(0, 0, 1000, 400)
    fitted = box.fit(2.0)  # width:height 2:1
    assert fitted.rect.width / fitted.rect.height == pytest.approx(2.0, abs=0.01)
    assert fitted.rect.centerx == box.rect.centerx
    assert fitted.rect.centery == box.rect.centery

    # a very wide box fitting a taller aspect should be limited by height
    box2 = Box.of(0, 0, 2000, 100)
    fitted2 = box2.fit(1.0)
    assert fitted2.rect.height <= box2.rect.height
    assert fitted2.rect.width == pytest.approx(fitted2.rect.height, abs=1)


def test_box_pad_and_inset():
    box = Box.of(0, 0, 200, 100)
    padded = box.pad(10)
    assert padded.rect.width == 180
    assert padded.rect.height == 80

    inset = box.inset(left=10, top=5, right=20, bottom=15)
    assert inset.rect.left == 10
    assert inset.rect.top == 5
    assert inset.rect.width == 200 - 10 - 20
    assert inset.rect.height == 100 - 5 - 15


def test_box_top_and_bottom_slice():
    box = Box.of(0, 0, 200, 100)
    top = box.top_slice(20)
    bottom = box.bottom_slice(20)
    assert top.rect.top == box.rect.top
    assert top.rect.height == 20
    assert bottom.rect.bottom == box.rect.bottom
    assert bottom.rect.height == 20
