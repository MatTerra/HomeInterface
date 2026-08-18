"""Application configuration.

One YAML file drives display, theme, backend and which plan to load.  Every
key has a default, so the app runs with no config at all (mock backend,
built-in demo plan).
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "display": {
        # the target panel: 480x320 SPI TFT, landscape
        "width": 480,
        "height": 320,
        "fullscreen": False,
        "resizable": True,
        # an SPI bus pushes maybe 20-30 full frames/s; asking for 60 just
        # burns CPU on frames the panel will never show
        "fps": 30,
        "vsync": True,
        "density": 1.0,
        "cursor": True,
        "title": "HOME INTERFACE",
        # how the frame reaches the panel:
        #   window - an SDL window (development machines)
        #   fbdev  - mmap an fbtft framebuffer and push RGB565 rows over SPI
        #   auto   - fbdev when a framebuffer exists and no desktop session does
        "driver": "auto",
        # framebuffer device; None probes /dev/fb1 then /dev/fb0
        "fbdev": None,
        # touch input: auto | none | /dev/input/eventN
        "touch": "auto",
        # the digitiser's orientation is independent of the framebuffer's
        # rotate= parameter; tools/touchcal.py finds these
        "touch_calibration": {"swap_xy": False, "invert_x": False, "invert_y": False},
    },
    "plan": "config/floorplan.example.yaml",
    # sync_registry only bites on the homeassistant backend; it reconciles HA's
    # floors/areas/labels with the plan on every connect
    "backend": {"kind": "mock", "chaos": True, "sync_registry": True},
    "theme": {},
    "overview": {},
    # shell: which whole set of screens runs.
    #   stock  - the scale drawing plus the side rail
    #   alt    - the small-screen drill-down (--alternative)
    #   custom - the screens declared in the dashboard file (--custom)
    "ui": {"shell": "stock"},
    # only read by the custom shell; --custom overrides it
    "dashboard": "config/dashboard.yaml",
    "start_screen": "plan",
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None) -> dict[str, Any]:
    """Read ``path`` (if given and present) merged over :data:`DEFAULTS`.

    Always returns a deep copy: callers mutate the result in place (the CLI
    writes ``config["display"]["width"]``), and a shallow copy would let that
    edit reach into the module-level ``DEFAULTS`` and persist for the whole
    process.
    """
    if path is None:
        return deepcopy(DEFAULTS)
    file = Path(path)
    if not file.exists():
        return deepcopy(DEFAULTS)
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{file}: config root must be a mapping")
    merged = _merge(deepcopy(DEFAULTS), data)
    merged["_path"] = str(file.resolve())
    merged["_root"] = str(file.resolve().parent.parent)
    return merged


def resolve_path(config: dict[str, Any], value: str | None) -> Path | None:
    """Resolve a config-relative path against the config file's project root."""
    if not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    root = config.get("_root")
    if root:
        anchored = Path(root) / candidate
        if anchored.exists():
            return anchored
    return candidate
