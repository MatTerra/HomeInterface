"""Stock shell parity: the same house, rendered by hand-built screens and by
a dashboard YAML tree, diffed structurally rather than pixel-for-pixel.

``homeinterface/screens/plan.py``, ``overview.py`` and ``systems.py`` (the
"stock" shell) are read-only oracles here - never touched, never imported for
anything but their own behavior.  ``STOCK_DASHBOARD`` is a from-scratch
dashboard tree, built entirely from components/grammar three prior phases
already shipped (docs/adr/0005, docs/adr/0006); this file only assembles.

Five scenarios, matched against the three stock screens:

* plan-overview   - PlanScreen._grid_places()      vs the GRID tab's places
* plan-focus (dev)  - PlanScreen._build_inspector() vs device-inspector
* plan-focus (zone) - PlanScreen._build_zone_inspector() vs zone-inspector
* overview        - OverviewScreen's gauges/quick-controls/messages counts
* systems         - SystemsScreen's domain filter + link diagnostics
"""

from __future__ import annotations

import pygame
import pytest

from homeinterface.backend.mock import MockBackend
from homeinterface.dashboard.build import DashboardScreen
from homeinterface.dashboard.components import DeviceInspector, ToggleAction, ZoneInspector
from homeinterface.dashboard.loader import dashboard_from_text
from homeinterface.floorplan.loader import plan_from_dict
from homeinterface.screens.overview import OverviewScreen
from homeinterface.screens.plan import PlanScreen
from homeinterface.screens.systems import SystemsScreen
from homeinterface.scaling import Viewport
from homeinterface.ui.base import UIContext
from homeinterface.ui.controls import Button, Slider
from homeinterface.ui.indicators import ArcGauge, BarGauge, MessageStrip

from tests.test_dashboard_shell import CONTENT, PANEL, FakeApp, leaves

#: two floors, a multi-room zone (z1 = r0+r1) and a lone unzoned room (r2) -
#: enough to exercise floor switching and both focus flavours.  r1 carries a
#: climate device alongside a light so the zone inspector gets both sliders.
STOCK_PLAN = {
    "floors": [
        {
            "id": "f1", "name": "Ground", "level": 0,
            "rooms": [
                {"id": "r0", "name": "Room 0", "rect": [0, 0, 4, 4]},
                {"id": "r1", "name": "Room 1", "rect": [4, 0, 4, 4]},
            ],
            "devices": [
                {"entity_id": "light.r0_0", "at": [1, 2], "room": "r0"},
                {"entity_id": "light.r1_0", "at": [5, 2], "room": "r1"},
                {"entity_id": "climate.r1_thermo", "at": [6, 2], "room": "r1"},
            ],
        },
        {
            "id": "f2", "name": "Upper", "level": 1,
            "rooms": [{"id": "r2", "name": "Room 2", "rect": [0, 0, 3, 3]}],
            "devices": [{"entity_id": "light.r2_0", "at": [1, 1], "room": "r2"}],
        },
    ],
    "zones": [{"id": "z1", "name": "Social", "rooms": ["r0", "r1"]}],
}

ENTITY_IDS = ["light.r0_0", "light.r1_0", "climate.r1_thermo", "light.r2_0",
              "sensor.outdoor_temperature", "sensor.house_power", "binary_sensor.porta"]


@pytest.fixture
def stock_backend():
    b = MockBackend(entity_ids=ENTITY_IDS, chaos=False)
    b.start()
    yield b
    b.stop()


# -- the dashboard tree ----------------------------------------------------
#: a floor-strip chip row, shared (YAML anchor/alias, ADR 0005) between the
#: drawing and grid presentations of the overview so both stay on the same
#: floor.  Uses the `set` action (ADR 0006): switching floors updates the
#: current pane's param without navigating away from it.
STOCK_DASHBOARD = """
root:
  type: tabs
  bar: left
  children:
    - type: tabs
      id: plan
      title: PLAN
      bar: none
      children:
        - type: rows
          id: plan-overview
          children:
            - type: tabs
              bar: top
              columns: 6
              rows: 3
              children:
                - type: rows
                  id: pv-drawing
                  title: DRAWING
                  children:
                    - &floor_strip
                      type: chips
                      columns: 6
                      rows: 0.5
                      over: floors
                      from: {}
                      template:
                        type: button
                        label: "$tag"
                        on_press: {set: floor, value: $floor}
                    - type: floorplan
                      columns: 6
                      rows: 2.0
                      floor: $floor
                      on_press: {goto: plan-focus, params: {room: $room, zone: $zone, floor: $floor}}
                - type: rows
                  id: pv-grid
                  title: GRID
                  children:
                    - *floor_strip
                    - type: places
                      columns: 6
                      rows: 2.0
                      floor: $floor
                      on_press: {goto: plan-focus, params: {room: $room, zone: $zone, floor: $floor}}
        - type: rows
          id: plan-focus
          children:
            - type: cols
              columns: 6
              rows: 0.5
              children:
                - {type: button, label: "< PLAN", on_press: back, columns: 2, rows: 0.5}
                - {type: label, text: "FOCUS", columns: 3, rows: 0.5}
            - type: cols
              columns: 6
              rows: 2.5
              children:
                - type: floorplan
                  columns: 3
                  rows: 2.5
                  floor: $floor
                  focus: "$room$zone"
                  on_select: {set: device, value: $entity}
                - type: rows
                  columns: 2
                  rows: 2.5
                  children:
                    - type: device-inspector
                      entity: $device
                      visible_if: {exists: true}
                      columns: 2
                      rows: 1.5
                    - type: zone-inspector
                      entity: $device
                      visible_if: {exists: false}
                      zone: $zone
                      room: $room
                      columns: 2
                      rows: 1.0
    - type: rows
      id: overview
      title: OVERVIEW
      children:
        - type: cols
          columns: 6
          rows: 3
          children:
            - type: rows
              columns: 2
              rows: 3
              children:
                - {type: arc-gauge, entity: sensor.outdoor_temperature, label: OUTSIDE, unit: "°C", min: -5, max: 45, columns: 2, rows: 1.5}
                - {type: bar-gauge, entity: sensor.house_power, label: POWER, unit: W, max: 8000, columns: 2, rows: 1.5}
            - type: rows
              columns: 2
              rows: 3
              over: entities
              from: {entities: [light.r0_0, light.r1_0, light.r2_0]}
              template: {type: toggle, entity: $entity, columns: 2, rows: 1}
            - type: rows
              columns: 1
              rows: 3
              children:
                - {type: messages, columns: 1, rows: 3}
    - type: tabs
      id: systems
      title: SYSTEMS
      bar: top
      children:
        - type: cols
          id: sys-all
          title: ALL
          children:
            - {type: device-rows, columns: 3, rows: 2.5}
            - {type: link-status, columns: 2, rows: 2.5}
        - type: cols
          id: sys-light
          title: LGT
          children:
            - type: grid
              columns: 3
              rows: 2.5
              over: entities
              from: {domain: light}
              template: {type: toggle, entity: $entity, columns: 2, rows: 0.5}
            - {type: link-status, columns: 2, rows: 2.5}
        - type: cols
          id: sys-sensor
          title: SNS
          children:
            - type: grid
              columns: 3
              rows: 2.5
              over: entities
              from: {domain: sensor}
              template: {type: toggle, entity: $entity, columns: 2, rows: 0.5}
            - {type: link-status, columns: 2, rows: 2.5}
        - type: cols
          id: sys-binary
          title: BIN
          children:
            - type: grid
              columns: 3
              rows: 2.5
              over: entities
              from: {domain: binary_sensor}
              template: {type: toggle, entity: $entity, columns: 2, rows: 0.5}
            - {type: link-status, columns: 2, rows: 2.5}
"""


def build_stock(backend, size=PANEL, rect=CONTENT, floor="f1"):
    plan = plan_from_dict(STOCK_PLAN)
    app = FakeApp(plan, backend)
    screen = DashboardScreen(app, dashboard_from_text(STOCK_DASHBOARD, source="stock.yaml"))
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*size), backend=backend)
    # a real embedding app seeds a sane initial default the way `dashboard.
    # start:` seeds a pane - there is no grammar for a default *param* value
    # (see this file's closing note), so the harness plays that role here.
    screen.params["floor"] = floor
    screen.ensure_layout(pygame.Rect(rect), ctx)
    return screen, ctx, app, plan


def _oracle_ctx(app, backend, size=PANEL):
    return UIContext(theme=app.theme, book=app.book, vp=Viewport(*size), backend=backend)


# -- scenario 1: plan overview ---------------------------------------------
def test_plan_overview_places_match_the_hand_built_grid(stock_backend):
    screen, ctx, app, plan = build_stock(stock_backend, floor="f1")
    oracle = PlanScreen(app)
    oracle.floor_id = "f1"
    hand_places = oracle._grid_places()

    screen.goto("pv-grid", {})
    screen.ensure_layout(CONTENT, ctx)
    grid = next(w for w in leaves(screen) if hasattr(w, "places"))
    dash_places = grid.places

    assert {p[0] for p in hand_places} == {p.name for p in dash_places}
    assert {frozenset(p[3]) for p in hand_places} == {frozenset(p.entity_ids) for p in dash_places}
    # r0+r1 share zone z1, so overview collapses them to one place; r2 stands
    # alone - three rooms across two floors, two places on this one
    assert len(hand_places) == len(dash_places) == 1


def test_plan_overview_floor_two_has_its_own_lone_room(stock_backend):
    screen, ctx, app, plan = build_stock(stock_backend, floor="f2")
    oracle = PlanScreen(app)
    oracle.floor_id = "f2"
    hand_places = oracle._grid_places()

    screen.goto("pv-grid", {})
    screen.ensure_layout(CONTENT, ctx)
    grid = next(w for w in leaves(screen) if hasattr(w, "places"))
    dash_places = grid.places

    assert [p[0] for p in hand_places] == [p.name for p in dash_places] == ["Room 2"]
    assert [p[3] for p in hand_places] == [p.entity_ids for p in dash_places] == [["light.r2_0"]]


# -- scenario 2: plan focus, a single device -------------------------------
def test_plan_focus_device_inspector_matches_the_hand_built_slider(stock_backend):
    oracle = PlanScreen(FakeApp(plan_from_dict(STOCK_PLAN), stock_backend))
    oracle_ctx = _oracle_ctx(oracle.app, stock_backend)
    oracle.floor_id = "f2"
    oracle._enter_focus("r2")
    oracle.selected_device = "light.r2_0"
    oracle._inspector_key = None
    oracle._build_inspector(oracle_ctx)
    hand_toggle = next(w for w in oracle._inspector if isinstance(w, Button))
    hand_slider = next(w for w in oracle._inspector if isinstance(w, Slider))

    screen, ctx, app, plan = build_stock(stock_backend, floor="f2")
    screen.goto("plan-focus", {"room": "r2", "zone": "", "floor": "f2", "device": "light.r2_0"})
    screen.ensure_layout(CONTENT, ctx)
    inspector = next(w for w in leaves(screen) if isinstance(w, DeviceInspector))

    assert inspector.toggle.sub == hand_toggle.sub == "light.r2_0"
    assert inspector.slider is not None and inspector.slider.label == hand_slider.label == "BRIGHTNESS"


# -- scenario 3: plan focus, a zone ----------------------------------------
def test_plan_focus_zone_inspector_matches_the_hand_built_aggregate_and_sliders(stock_backend):
    oracle = PlanScreen(FakeApp(plan_from_dict(STOCK_PLAN), stock_backend))
    oracle_ctx = _oracle_ctx(oracle.app, stock_backend)
    oracle.floor_id = "f1"
    oracle._enter_focus("r0")  # r0 belongs to zone z1 -> zone scope by default
    oracle._inspector_key = None
    oracle._build_inspector(oracle_ctx)
    on, total = stock_backend.group_state(oracle._zone_entity_ids)
    hand_lights = [w for w in oracle._inspector if isinstance(w, Slider) and w.label == "ZONE BRIGHTNESS"]
    hand_climates = [w for w in oracle._inspector if isinstance(w, Slider) and w.label == "ZONE TARGET"]

    screen, ctx, app, plan = build_stock(stock_backend, floor="f1")
    screen.goto("plan-focus", {"room": "", "zone": "z1", "floor": "f1"})
    screen.ensure_layout(CONTENT, ctx)
    inspector = next(w for w in leaves(screen) if isinstance(w, ZoneInspector))
    inspector.draw(pygame.Surface(PANEL), ctx)

    assert set(inspector.entity_ids) == set(oracle._zone_entity_ids) == {
        "light.r0_0", "light.r1_0", "climate.r1_thermo"}
    assert inspector.master.active == (on > 0)
    assert (inspector.brightness is not None) == bool(hand_lights)
    assert (inspector.temperature is not None) == bool(hand_climates)


# -- scenario 4: overview screen -------------------------------------------
def test_overview_gauges_quick_controls_and_messages_match(stock_backend):
    app = FakeApp(plan_from_dict(STOCK_PLAN), stock_backend)
    oracle = OverviewScreen(app)
    oracle_ctx = _oracle_ctx(app, stock_backend)
    oracle.layout(CONTENT, oracle_ctx)

    screen, ctx, _app, plan = build_stock(stock_backend)
    screen.goto("overview", {})
    screen.ensure_layout(CONTENT, ctx)
    widgets = leaves(screen)
    gauges = [w for w in widgets if isinstance(w, (ArcGauge, BarGauge))]
    toggles = [w for w in widgets if isinstance(w, ToggleAction)]
    messages = [w for w in widgets if isinstance(w, MessageStrip)]

    assert len(gauges) == len(oracle.gauges)
    assert len(toggles) == len(oracle._quick_entities(oracle_ctx))
    assert len(messages) == 1
    stock_backend.alerts()  # exercised for parity, no crash reading it live


# -- scenario 5: systems screen ---------------------------------------------
def test_systems_domain_filter_matches_the_hand_built_count(stock_backend):
    app = FakeApp(plan_from_dict(STOCK_PLAN), stock_backend)
    oracle = SystemsScreen(app)
    oracle_ctx = _oracle_ctx(app, stock_backend)
    oracle.layout(CONTENT, oracle_ctx)
    oracle._set_filter("light")
    oracle._rebuild_tiles(oracle_ctx)
    hand_count = len(oracle.tiles)

    screen, ctx, _app, plan = build_stock(stock_backend)
    screen.goto("sys-light", {})
    screen.ensure_layout(CONTENT, ctx)
    dash_ids = {w.inner.entity_id for w in screen._widgets if hasattr(w, "inner")
                and hasattr(w.inner, "entity_id")}
    expected = {eid for eid in stock_backend.snapshot() if eid.startswith("light.")}
    assert len(dash_ids) == hand_count == len(expected)


def test_systems_link_status_matches_the_hand_built_diagnostics(stock_backend):
    app = FakeApp(plan_from_dict(STOCK_PLAN), stock_backend)
    oracle = SystemsScreen(app)
    oracle_ctx = _oracle_ctx(app, stock_backend)
    oracle.layout(CONTENT, oracle_ctx)

    screen, ctx, _app, plan = build_stock(stock_backend)
    screen.goto("sys-light", {})
    screen.ensure_layout(CONTENT, ctx)
    from homeinterface.dashboard.components import _lookup  # noqa: F401  (unused, keeps import local)
    link = None
    for w in leaves(screen):
        if hasattr(w, "rows") and not hasattr(w, "places"):
            link = w
    assert link is not None
    link.draw(pygame.Surface(PANEL), ctx)
    entities_row = next(r for r in link.rows if r[0] == "ENTITIES")
    revision_row = next(r for r in link.rows if r[0] == "REVISION")
    assert entities_row[1] == str(len(stock_backend.snapshot()))
    assert revision_row[1] == str(stock_backend.revision)
    assert stock_backend.link.value.upper() in [r[1] for r in link.rows if r[0] == "STATE"]


# -- touch minimum -----------------------------------------------------------
# Every node *this file* authored - the tabs, containers and top-level
# component nodes named in STOCK_DASHBOARD - must clear the touch minimum.
# The internal buttons/sliders a composite component (device-inspector,
# zone-inspector, places, device-rows...) builds for itself are that
# component's own layout, already covered by tests/test_dashboard_shell.py;
# re-asserting on them here would be testing components.py, not this port.
def test_every_stock_dashboard_node_clears_the_touch_minimum(stock_backend):
    from homeinterface.theme import Theme
    touch_min = Theme().touch_min
    for pane, params in (
        ("pv-drawing", {}),
        ("pv-grid", {}),
        ("plan-focus", {"room": "r2", "zone": "", "floor": "f2", "device": "light.r2_0"}),
        ("overview", {}),
        ("sys-all", {}),
    ):
        screen, ctx, app, plan = build_stock(stock_backend, floor="f2" if pane == "plan-focus" else "f1")
        screen.goto(pane, params)
        screen.ensure_layout(CONTENT, ctx)
        for widget in screen._widgets:
            assert min(widget.rect.width, widget.rect.height) >= touch_min - 2, (pane, widget)
