"""Colour and typography, following Airbus ECAM/Embraer EICAS conventions.

Colour in an avionics display is *semantic*, never decorative.  Keep the
meanings below when adding widgets; that discipline is what makes the panel
read as an aircraft system rather than a Star Trek prop.

    GREEN    system normal, powered, in use
    WHITE    titles, labels, selected-but-neutral
    CYAN     units, setpoints, data the operator may change
    AMBER    caution - abnormal but not immediately dangerous
    RED      warning - requires immediate action
    MAGENTA  targets and constraints (setpoint markers on gauges)
    GREY     inoperative, unpowered, unavailable
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

RGB = tuple[int, int, int]


def _rgb(value: Any, fallback: RGB) -> RGB:
    """Accept ``"#RRGGBB"``, ``[r, g, b]`` or a tuple from YAML."""
    if value is None:
        return fallback
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) != 6:
            raise ValueError(f"bad colour {value!r}")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    r, g, b = value
    return (int(r), int(g), int(b))


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """Linear blend; ``t=0`` -> a, ``t=1`` -> b."""
    t = max(0.0, min(1.0, t))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def dim(c: RGB, factor: float) -> RGB:
    return (round(c[0] * factor), round(c[1] * factor), round(c[2] * factor))


@dataclass(frozen=True)
class Theme:
    # -- surfaces --------------------------------------------------------
    background: RGB = (7, 10, 14)
    panel: RGB = (15, 21, 28)
    panel_alt: RGB = (21, 29, 38)
    rule: RGB = (44, 58, 72)
    rule_bright: RGB = (78, 99, 118)

    # -- semantic --------------------------------------------------------
    normal: RGB = (0, 214, 120)  # green
    text: RGB = (222, 232, 240)  # white
    data: RGB = (95, 199, 245)  # cyan
    caution: RGB = (255, 176, 32)  # amber
    warning: RGB = (255, 66, 56)  # red
    target: RGB = (219, 112, 255)  # magenta
    inop: RGB = (96, 110, 124)  # grey

    # -- typography (design units; scaled at render time) ----------------
    font_stack: tuple[str, ...] = (
        "bahnschriftcondensed",
        "bahnschrift",
        "eurostile",
        "dinalternate",
        "consolas",
        "dejavusansmono",
    )
    mono_stack: tuple[str, ...] = ("consolas", "dejavusansmono", "couriernew")

    # Sizes are pixels on the 480x320 reference panel, so these numbers are
    # what actually has to be legible on the hardware. Anything under 9 stops
    # resolving on a 3.5" TFT.
    size_micro: float = 9.0
    size_small: float = 11.0
    size_body: float = 14.0
    size_large: float = 19.0
    size_readout: float = 30.0
    size_hero: float = 42.0

    # -- geometry (design units) -----------------------------------------
    stroke: float = 1.0
    stroke_bold: float = 2.0
    chamfer: float = 5.0
    gap: float = 5.0
    pad: float = 7.0

    #: minimum touch target on its short edge. 40px on a 3.5" panel is about
    #: 6mm - the smallest thing a fingertip hits reliably.
    touch_min: float = 40.0

    #: annunciators blink at this rate (Hz) when a warning is active
    blink_hz: float = 1.0

    extras: dict[str, Any] = field(default_factory=dict)

    def status_color(self, level: str) -> RGB:
        return {
            "normal": self.normal,
            "ok": self.normal,
            "on": self.normal,
            "info": self.data,
            "caution": self.caution,
            "warning": self.warning,
            "inop": self.inop,
            "off": self.inop,
        }.get(level, self.text)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Theme":
        """Build a theme from a YAML fragment; unknown keys land in ``extras``."""
        base = cls()
        if not data:
            return base
        colour_fields = {
            f
            for f in (
                "background",
                "panel",
                "panel_alt",
                "rule",
                "rule_bright",
                "normal",
                "text",
                "data",
                "caution",
                "warning",
                "target",
                "inop",
            )
        }
        changes: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        for key, value in data.items():
            if key in colour_fields:
                changes[key] = _rgb(value, getattr(base, key))
            elif key in ("font_stack", "mono_stack"):
                changes[key] = tuple(value)
            elif hasattr(base, key):
                changes[key] = value
            else:
                extras[key] = value
        if extras:
            changes["extras"] = extras
        return replace(base, **changes)


DEFAULT_THEME = Theme()
