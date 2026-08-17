from __future__ import annotations

import pytest

from homeinterface.theme import Theme, mix


def test_from_dict_none_or_empty_returns_default():
    assert Theme.from_dict(None) == Theme()
    assert Theme.from_dict({}) == Theme()


def test_from_dict_parses_hex_rrggbb():
    theme = Theme.from_dict({"background": "#112233"})
    assert theme.background == (0x11, 0x22, 0x33)


def test_from_dict_parses_hex_without_hash():
    theme = Theme.from_dict({"background": "112233"})
    assert theme.background == (0x11, 0x22, 0x33)


def test_from_dict_parses_3_digit_hex():
    theme = Theme.from_dict({"warning": "#f80"})
    assert theme.warning == (0xFF, 0x88, 0x00)


def test_from_dict_parses_rgb_list():
    theme = Theme.from_dict({"normal": [10, 20, 30]})
    assert theme.normal == (10, 20, 30)


def test_from_dict_bad_hex_raises():
    with pytest.raises(ValueError):
        Theme.from_dict({"background": "#12"})


def test_from_dict_unknown_keys_land_in_extras():
    theme = Theme.from_dict({"totally_made_up": 42, "another_one": "x"})
    assert theme.extras == {"totally_made_up": 42, "another_one": "x"}
    # known fields keep their defaults
    assert theme.background == Theme().background


def test_from_dict_known_non_colour_fields_are_applied():
    theme = Theme.from_dict({"stroke": 5.0, "blink_hz": 2.0})
    assert theme.stroke == 5.0
    assert theme.blink_hz == 2.0


def test_from_dict_font_stack_becomes_tuple():
    theme = Theme.from_dict({"font_stack": ["a", "b"]})
    assert theme.font_stack == ("a", "b")
    assert isinstance(theme.font_stack, tuple)


def test_status_color_mapping():
    theme = Theme()
    assert theme.status_color("normal") == theme.normal
    assert theme.status_color("ok") == theme.normal
    assert theme.status_color("on") == theme.normal
    assert theme.status_color("info") == theme.data
    assert theme.status_color("caution") == theme.caution
    assert theme.status_color("warning") == theme.warning
    assert theme.status_color("inop") == theme.inop
    assert theme.status_color("off") == theme.inop
    # unknown level falls back to text colour
    assert theme.status_color("something_else") == theme.text


def test_mix_endpoints():
    a = (0, 0, 0)
    b = (100, 200, 255)
    assert mix(a, b, 0.0) == a
    assert mix(a, b, 1.0) == b
    # clamps outside [0, 1]
    assert mix(a, b, -1.0) == a
    assert mix(a, b, 2.0) == b


def test_mix_midpoint():
    a = (0, 0, 0)
    b = (100, 100, 100)
    assert mix(a, b, 0.5) == (50, 50, 50)
