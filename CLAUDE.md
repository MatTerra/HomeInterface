# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A house control panel built with pygame, styled after Airbus ECAM / Embraer EICAS avionics displays (dark ground, angular vector geometry, semantic colour). Reads Home Assistant entities (or a mock backend) and renders them onto a floor plan, a vitals overview and an entity register. Target hardware is a Raspberry Pi 3 driving a 480x320 SPI touch panel, so it also has to run on a desktop dev window at any resolution.

Two requirements shape almost everything: **resolution independence** (no bitmaps anywhere — every shape is drawn from geometry at the current pixel size) and a **shareable floor-plan YAML config** that knows nothing about pygame or Home Assistant.

## Commands

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows
pip install -r requirements.txt
python main.py                          # run (mock backend by default)

pip install -r requirements-dev.txt
python -m pytest tests -q               # run all tests
python -m pytest tests/test_scaling.py -q          # single file
python -m pytest tests/test_scaling.py -q -k name   # single test

python tools/svg2plan.py casa.svg --px-per-unit 100 --inspect          # inspect an SVG import
python tools/svg2plan.py casa.svg --px-per-unit 100 -o config/floorplan.yaml
python tools/touchcal.py                # touchscreen calibration (on-device)
```

Useful `main.py` flags: `--backend {mock,homeassistant}`, `--alternative` (small-screen shell), `--driver {auto,window,fbdev}`, `--density FLOAT` (touch panel UI scale), `-c/--config PATH`.

Tests are headless (`SDL_VIDEODRIVER=dummy`, set in `tests/conftest.py` before pygame is imported) — no display needed to run the suite.

## Architecture

```
homeinterface/
  app.py            output, chrome (title/rail/footer), main loop, argparse entry, SCREEN_TYPES/ALT_SCREEN_TYPES
  config.py         load_config(): YAML merged over DEFAULTS
  fbdev.py          SPI panel: RGB565 row-diffing framebuffer, evdev touch (no X/Wayland)
  scaling.py        Viewport (design units -> px) and Box (fractional layout)
  theme.py          Theme dataclass, colour parsing, status_color()
  draw.py           vector primitives: chamfered rects, hairline grids, ticks
  fonts.py          FontBook: resolves/rasterises/caches text at exact px size
  floorplan/
    model.py        FloorPlan/Floor/Room/Device/Opening/Wall, polygon math
    loader.py        YAML <-> model, PlanError, rect: shorthand
    svg_import.py     Inkscape-label-driven SVG -> FloorPlan
    renderer.py        FloorRenderer: draws a Floor into a rect, PlanView
  backend/
    base.py          Backend ABC, Entity/Link/Alert, thread-safe snapshot store
    mock.py           MockBackend: drifting sensors, instant commands
    homeassistant.py  WebSocket backend, auto-reconnect
  ui/
    base.py          Widget/Pressable/WidgetGroup, UIContext
    controls.py       Button, Slider, ToggleButton, TabStrip
    indicators.py      Panel, ArcGauge, BarGauge, Readout, StatusLamp, EntityTile
  screens/
    base.py          Screen contract (layout/draw/handle/lifecycle)
    plan.py           floor plan + device inspector ("stock" shell)
    alt.py             --alternative shell: drill-down places -> place -> device
    overview.py         ECAM-style vitals: gauges, quick controls, annunciators
    systems.py           entity register + link diagnostics
  dashboard/          custom shell: declarative YAML-defined screens (see config/dashboard.yaml)
    schema.py, loader.py, registry.py, components.py, build.py
```

**Key seam: the UI never talks to Home Assistant directly.** A `Backend` runs its own I/O on a background thread (WebSocket event loop for `HomeAssistantBackend`, a drift-simulation thread for `MockBackend`) and publishes immutable `Entity` snapshots behind a lock. Screens only call `backend.get()` / `.snapshot()` / `.by_domain()` to read and `backend.call()` / `.toggle()` / `.set_brightness()` etc. to write (fire-and-forget; failures surface as footer alerts). The render loop never blocks on the network.

**Scaling has two deliberately separate mechanisms** (`scaling.py`): `Viewport.u()` is a uniform design-unit scale for "ink" (strokes, chamfers, fonts, gaps), computed against a 1920x1080 reference canvas. `Box` is fractional sub-rectangle layout (`.cols()`, `.rows()`, `.grid()`, `.pad()`, `.fit()`) — never letterboxed, always a fraction of the real surface. Widgets are positioned with `Box` and drawn with `ctx.u()` / `ctx.px()` / `ctx.font_px()` — never a raw pixel constant.

**Three shells** select the whole screen set at startup and don't mix (the dashboard shell is the target; stock and alt predate it and are on their way out - maintain them, don't extend them, see `docs/backlog.md`): stock (`plan.py`/`overview.py`/`systems.py`, side rail nav), alt (`screens/alt.py`, drill-down `places -> place -> device`, bottom bar, no pan/zoom/scroll — built for small resistive touch panels), and the dashboard/custom shell (screens declared in YAML rather than code, see `homeinterface/dashboard/` and ADRs below).

**Input is single touch** (the panel is resistive - `fbdev.py` emits only `MOUSEBUTTONDOWN`/`MOUSEMOTION`/`MOUSEBUTTONUP`). Every function must be reachable with a **tap, or a press-drag-release inside one widget**. `MOUSEWHEEL`, buttons 2/3, hover (`ctx.pointer` without a press) and pinch may exist as desktop development shortcuts, never as the only path to a function; long-press only as a last resort. Content that does not fit is **paginated, never scrolled**, and no on-screen hint may name an input the panel lacks. See `docs/adr/0004-no-gestures-on-resistive-touch.md`.

**Colour is semantic, never decorative** (ECAM discipline): green = normal/powered, white = titles/neutral, cyan = units/setpoints, amber = caution, red = warning, magenta = target/constraint markers, grey = inoperative. New widgets must map state through `Theme.status_color()`, not a hardcoded RGB.

**Floor plan model** (`floorplan/model.py`) is pure geometry — no pygame, no Home Assistant — so the same YAML renders identically for anyone. Coordinates are x-right/y-down (metres by default), shared across all floors of a house so storeys stay aligned. `svg2plan.py` regenerates the YAML from an Inkscape/QCAD source drawing (labels carry meaning via colon-separated `floor:`/`room:`/`wall`/`door`/`device:` tags) — treat the YAML as a disposable build artefact relative to the SVG.

Longer-form docs: `docs/floorplan.md` (plan YAML + SVG import), `docs/dashboard.md` (dashboard format), `docs/shells.md` (stock and alt), `docs/architecture.md` (scaling and colour, long form), `docs/backlog.md` (known gaps), `docs/raspberry-pi.md` (building the panel). The README is an index, not a reference - don't grow it.

Terminology (floor/room/zone/place/device, dashboard cell/unit/span/node/component/binding/etc.) is defined in `CONTEXT.md` — check it before naming something new in the dashboard or spatial model. Architectural decisions (e.g. the 6x3 author grid, no-templating bindings, one-node-tree) are recorded in `docs/adr/`.

## Adding a screen or widget

- New screen: subclass `Screen` (`screens/base.py`), set `key`/`title`/`subtitle`, implement `layout(rect, ctx)` (+ `draw`/`handle` if needed beyond default widget-group forwarding), register in `SCREEN_TYPES` (`app.py`).
- New widget: subclass `Widget` or `Pressable` (`ui/base.py`), implement `layout(rect)` + `draw(surface, ctx)` using `draw.py` primitives and `fonts.blit_text` (never a bitmap blit), size via `ctx.u()`/`ctx.px()`/`ctx.font_px()`, colour via `ctx.theme.status_color()`.
- New dashboard component: add the name to `COMPONENTS` in `dashboard/registry.py` **and** register its builder in `dashboard/components.py` - a name in one without the other fails at build time. Check `CONTEXT.md` before naming it.

## Deploying

`deploy/deploy.sh` / `deploy/deploy.ps1` push to the Pi over SSH; `deploy/homeinterface.service` is the systemd unit. Full panel walkthrough (OS image, `config.txt` overlay, calibration, tuning) is in `docs/raspberry-pi.md`.
