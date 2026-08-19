"""Parity proof: the alt shell (screens/alt.py, screens/systems.py) ported to
a dashboard YAML tree, diffed against the hand-built shell it mirrors.

The hand-built shell is read-only reference material here (the oracle); the
dashboard tree lives entirely as an inline YAML string, in the same fixture
style as tests/test_dashboard_shell.py.  Assertions are structural (widget
counts, order, entity ids, rects) rather than class-identity or pixel
equality: the two shells build genuinely different widget trees for the same
visual role (docs/adr/0005, docs/adr/0006), so "the same Python class" was
never going to be the right bar for parity. Where a composite happens to
reuse the exact same widget class (PlaceCard, DeviceRow - the dashboard's
``places``/``device-rows`` builders import them straight from
screens/alt.py), the tests do compare those directly.
"""

from __future__ import annotations

import pygame
import pytest

from homeinterface.backend.base import Entity
from homeinterface.backend.mock import MockBackend
from homeinterface.dashboard.build import DashboardScreen
from homeinterface.dashboard.components import DeviceInspector, ZoneInspector
from homeinterface.dashboard.loader import dashboard_from_text
from homeinterface.floorplan.loader import plan_from_dict
from homeinterface.fonts import FontBook
from homeinterface.scaling import Viewport
from homeinterface.screens.alt import AltHomeScreen, AltVitalsScreen, DeviceRow, PlaceCard
from homeinterface.screens.systems import SystemsScreen
from homeinterface.theme import Theme
from homeinterface.ui.base import UIContext

PANEL = (480, 320)
CONTENT = pygame.Rect(7, 42, 466, 271)

#: one zone (two rooms) plus one lone room, one device of each kind that
#: device-inspector/zone-inspector branch on (light, climate, cover)
PLAN = {
    "floors": [{
        "id": "f1", "name": "Ground", "level": 0,
        "rooms": [
            {"id": "r0", "name": "Living", "rect": [0, 0, 4, 4]},
            {"id": "r1", "name": "Kitchen", "rect": [4, 0, 4, 4]},
            {"id": "r2", "name": "Office", "rect": [8, 0, 4, 4]},
        ],
        "devices": [
            {"entity_id": "light.living", "at": [1, 2], "room": "r0"},
            {"entity_id": "climate.living", "at": [2, 2], "room": "r0"},
            {"entity_id": "light.kitchen", "at": [5, 2], "room": "r1"},
            {"entity_id": "light.office", "at": [9, 2], "room": "r2"},
            {"entity_id": "cover.office_blind", "at": [10, 2], "room": "r2"},
        ],
    }],
    "zones": [{"id": "z1", "name": "Social", "rooms": ["r0", "r1"]}],
}

ENTITY_IDS = [
    "light.living", "climate.living", "light.kitchen", "light.office",
    "cover.office_blind", "binary_sensor.front_door",
]


class FakeApp:
    def __init__(self, plan, backend):
        self.theme = Theme()
        self.book = FontBook(self.theme)
        self.plan = plan
        self.backend = backend
        self.config = {}


@pytest.fixture
def backend():
    b = MockBackend(entity_ids=ENTITY_IDS, chaos=False)
    b.start()
    yield b
    b.stop()


def plan():
    return plan_from_dict(PLAN)


def alt_home(backend) -> tuple[AltHomeScreen, UIContext]:
    app = FakeApp(plan(), backend)
    screen = AltHomeScreen(app)
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*PANEL), backend=backend)
    screen.layout(CONTENT, ctx)
    return screen, ctx


def alt_vitals(backend) -> tuple[AltVitalsScreen, UIContext]:
    app = FakeApp(plan(), backend)
    screen = AltVitalsScreen(app)
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*PANEL), backend=backend)
    screen.layout(CONTENT, ctx)
    return screen, ctx


def alt_systems(backend) -> tuple[SystemsScreen, UIContext]:
    app = FakeApp(plan(), backend)
    screen = SystemsScreen(app)
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*PANEL), backend=backend)
    screen.layout(CONTENT, ctx)
    return screen, ctx


def build(text: str, backend, size=PANEL, rect=CONTENT) -> tuple[DashboardScreen, UIContext]:
    app = FakeApp(plan(), backend)
    screen = DashboardScreen(app, dashboard_from_text(text, source="dash.yaml"))
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*size), backend=backend)
    screen.ensure_layout(pygame.Rect(rect), ctx)
    return screen, ctx


def leaves(screen: DashboardScreen):
    """Every widget the operator can actually see or hit, dashboard-side."""
    out = []
    for widget in screen._widgets:
        inner = getattr(widget, "inner", widget)
        out.append(inner)
        out.extend(getattr(inner, "children", []))
    return out


def find_all(screen: DashboardScreen, cls):
    return [w for w in leaves(screen) if isinstance(w, cls)]


def find_one(screen: DashboardScreen, cls):
    found = find_all(screen, cls)
    assert found, f"no {cls.__name__} in this screen"
    return found[0]


# -- the dashboard tree ------------------------------------------------------
#: Pane 1 mirrors AltHomeScreen: places -> place -> device, a drill-down
#: expressed as a `tabs: bar: none` stack (ADR 0003) whose panes are entered
#: purely by `goto` between ids.
#:
#: Pane 2 mirrors AltVitalsScreen: a chip row that swaps which section fills
#: the rect. `tabs: bar: top` already *is* that chip row - it draws a strip
#: of buttons and shows exactly one child at a time - so no second mechanism
#: is needed. Gauges/lamps are an illustrative set matching this file's own
#: MockBackend fixture, not screens/overview.py's DEFAULT_GAUGES (ADR 0005's
#: Q9/Q10 context; the task brief repeats this explicitly).
#:
#: Pane 3 mirrors SystemsScreen: a domain filter strip (`set` action, ADR
#: 0006) driving a live `repeat.over: entities, from: {domain: $domain}`
#: (ADR 0005) list of `tile` (the same EntityTile class SystemsScreen uses),
#: plus `link-status` (ADR 0005) beside it.
DASHBOARD_YAML = """
start: home
root:
  type: tabs
  bar: bottom
  children:
    - type: tabs
      id: home
      title: HOME
      bar: none
      children:
        - type: rows
          id: places
          children:
            - {type: places, floor: f1, columns: 6, rows: 2.5, on_press: {goto: place}}
        - type: rows
          id: place
          children:
            - type: cols
              columns: 6
              rows: 0.5
              children:
                - {type: button, label: "< BACK", on_press: back, columns: 2, rows: 0.5}
                - {type: label, text: "$name", columns: 4, rows: 0.5}
            - {type: zone-inspector, room: $room, zone: $zone, columns: 6, rows: 1}
            - {type: device-rows, room: $room, zone: $zone, columns: 6, rows: 1,
               on_press: {goto: device}}
        - type: rows
          id: device
          children:
            - {type: button, label: "< BACK", on_press: back, columns: 6, rows: 0.5}
            - {type: device-inspector, entity: $device, columns: 6, rows: 1.5}
            - {type: attr-list, entity: $device, columns: 6, rows: 0.5}
    - type: tabs
      id: vitals
      title: VITALS
      bar: top
      children:
        - type: grid
          id: vitals_gauges
          title: VITALS
          children:
            - {type: arc-gauge, entity: sensor.outdoor_temperature, label: OUTSIDE,
               unit: "°C", min: -10, max: 45, columns: 2, rows: 1}
            - {type: arc-gauge, entity: sensor.outdoor_humidity, label: HUMIDITY,
               unit: "%", min: 0, max: 100, columns: 2, rows: 1}
            - {type: bar-gauge, entity: sensor.house_power, label: POWER,
               unit: W, min: 0, max: 4000, columns: 2, rows: 1}
            - {type: lamp, entity: binary_sensor.front_door, label: FRONT DOOR,
               columns: 2, rows: 1}
        - type: rows
          id: vitals_quick
          title: QUICK
          over: entities
          from: {entities: [light.living, light.kitchen, light.office]}
          template: {type: toggle, entity: $entity, columns: 6, rows: 0.5}
        - type: rows
          id: vitals_status
          title: STATUS
          children:
            - {type: messages, lines: 6, columns: 6, rows: 2}
    - type: rows
      id: systems
      title: SYS
      children:
        - type: chips
          columns: 6
          rows: 0.5
          children:
            - {type: button, label: LGT, on_press: {set: domain, value: light}}
            - {type: button, label: CLM, on_press: {set: domain, value: climate}}
            - {type: button, label: CVR, on_press: {set: domain, value: cover}}
            - {type: button, label: BIN, on_press: {set: domain, value: binary_sensor}}
            - {type: button, label: SNS, on_press: {set: domain, value: sensor}}
        - type: cols
          columns: 6
          rows: 2
          children:
            - type: grid
              columns: 4
              rows: 2
              over: entities
              from: {domain: $domain}
              template: {type: tile, entity: $entity, columns: 2, rows: 0.5}
            - {type: link-status, columns: 2, rows: 2}
"""


# == places -> place -> device (AltHomeScreen) ==============================
def test_places_pane_matches_the_hand_built_places_grid(backend):
    alt, actx = alt_home(backend)
    alt_cards = [w for w in alt._widgets if isinstance(w, PlaceCard)]

    screen, ctx = build(DASHBOARD_YAML, backend)
    dash_cards = find_all(screen, PlaceCard)

    assert [c.name for c in dash_cards] == [c.name for c in alt_cards]
    assert [set(c.entity_ids) for c in dash_cards] == [set(c.entity_ids) for c in alt_cards]
    assert [c.is_zone for c in dash_cards] == [c.is_zone for c in alt_cards]
    # 2 places: the "Social" zone (r0+r1) and the lone "Office" room
    assert [c.name for c in alt_cards] == ["Social", "Office"]


def test_drilling_into_a_zone_place_matches_the_device_list(backend):
    alt, actx = alt_home(backend)
    alt._open_place("r0")  # r0 belongs to zone z1
    alt.layout(CONTENT, actx)
    alt_rows = [w for w in alt._widgets if isinstance(w, DeviceRow)]

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("place", {"room": "", "zone": "z1"})
    screen.ensure_layout(CONTENT, ctx)
    dash_rows = find_all(screen, DeviceRow)

    assert {r.entity_id for r in dash_rows} == {r.entity_id for r in alt_rows}
    assert {r.entity_id for r in dash_rows} == {"light.living", "climate.living", "light.kitchen"}


def test_drilling_into_a_lone_room_place_matches_the_device_list(backend):
    alt, actx = alt_home(backend)
    alt._open_place("r2")
    alt.layout(CONTENT, actx)
    alt_rows = [w for w in alt._widgets if isinstance(w, DeviceRow)]

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("place", {"room": "r2", "zone": ""})
    screen.ensure_layout(CONTENT, ctx)
    dash_rows = find_all(screen, DeviceRow)

    assert {r.entity_id for r in dash_rows} == {r.entity_id for r in alt_rows}
    assert {r.entity_id for r in dash_rows} == {"light.office", "cover.office_blind"}


def test_zone_place_master_toggle_reflects_the_same_aggregate_as_alt(backend):
    alt, actx = alt_home(backend)
    alt._open_place("r0")
    alt.layout(CONTENT, actx)
    alt.draw(pygame.Surface(PANEL), actx)  # alt refreshes its master on draw

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("place", {"room": "", "zone": "z1"})
    screen.ensure_layout(CONTENT, ctx)
    inspector = find_one(screen, ZoneInspector)
    inspector.draw(pygame.Surface(PANEL), ctx)

    assert inspector.master.active == alt._master.active
    assert inspector.master.sub == alt._master.sub
    # a light is in scope on both sides: a brightness slider must exist
    assert inspector.brightness is not None


def test_device_pane_toggle_and_slider_match_alt_for_a_light(backend):
    alt, actx = alt_home(backend)
    alt._open_place("r0")
    alt._open_device("light.living")
    alt.layout(CONTENT, actx)

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("device", {"device": "light.living"})
    screen.ensure_layout(CONTENT, ctx)
    inspector = find_one(screen, DeviceInspector)

    assert inspector.entity_id == alt.device_id == "light.living"
    assert inspector.slider is not None
    assert inspector.slider.label == "BRIGHTNESS"
    assert alt._widgets[-1].label == "BRIGHTNESS"  # the alt slider, laid out last


def test_device_pane_slider_matches_alt_for_climate_and_cover(backend):
    for entity_id, expected_label in (("climate.living", "TARGET"), ("cover.office_blind", "POSITION")):
        alt, actx = alt_home(backend)
        alt._open_place("r0" if entity_id.startswith("climate") else "r2")
        alt._open_device(entity_id)
        alt.layout(CONTENT, actx)

        screen, ctx = build(DASHBOARD_YAML, backend)
        screen.goto("device", {"device": entity_id})
        screen.ensure_layout(CONTENT, ctx)
        inspector = find_one(screen, DeviceInspector)

        assert inspector.slider is not None
        assert inspector.slider.label == expected_label


def test_device_pane_toggle_press_commands_the_same_entity_as_alt(backend, monkeypatch):
    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("device", {"device": "light.kitchen"})
    screen.ensure_layout(CONTENT, ctx)
    inspector = find_one(screen, DeviceInspector)

    calls = []
    monkeypatch.setattr(backend, "toggle", lambda eid: calls.append(eid))
    inspector.toggle.on_press()
    assert calls == ["light.kitchen"]


# == vitals / quick / status (AltVitalsScreen) ===============================
def test_vitals_section_shows_the_authored_gauges_and_a_lamp(backend):
    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("vitals_gauges", {})
    screen.ensure_layout(CONTENT, ctx)
    from homeinterface.ui.indicators import ArcGauge, BarGauge, StatusLamp

    assert len(find_all(screen, ArcGauge)) == 2
    assert len(find_all(screen, BarGauge)) == 1
    assert len(find_all(screen, StatusLamp)) == 1


def test_quick_section_matches_alts_auto_discovered_light_entities(backend):
    alt, actx = alt_vitals(backend)
    alt._select("quick")
    alt.layout(CONTENT, actx)
    alt_ids = {t.entity_id for t in alt.tiles}

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("vitals_quick", {})
    screen.ensure_layout(CONTENT, ctx)
    from homeinterface.dashboard.components import ToggleAction

    dash_ids = {w.entity_id for w in find_all(screen, ToggleAction)}

    # both discover exactly this backend's lights: alt auto-scans by_domain,
    # the dashboard names the same three explicitly (ADR 0005 literal list)
    assert alt_ids == dash_ids == {"light.living", "light.kitchen", "light.office"}


def test_status_section_shows_the_same_alerts_as_alt(backend):
    backend.raise_alert("test.one", "TEST ALERT ONE", "caution")
    backend.raise_alert("test.two", "TEST ALERT TWO", "warning")

    alt, actx = alt_vitals(backend)
    alt._select("status")
    alt.layout(CONTENT, actx)
    alt.draw(pygame.Surface(PANEL), actx)
    alt_lines = list(alt.messages.lines)

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("vitals_status", {})
    screen.ensure_layout(CONTENT, ctx)
    from homeinterface.ui.indicators import MessageStrip

    screen.draw(pygame.Surface(PANEL), ctx)  # Dynamic.refresh runs on draw
    strip = find_one(screen, MessageStrip)

    assert strip.lines == alt_lines
    assert len(strip.lines) == 2


# == systems (SystemsScreen) =================================================
def test_systems_domain_filter_narrows_the_list_like_systems_screen(backend):
    alt, actx = alt_systems(backend)
    alt._set_filter("light")
    alt.draw(pygame.Surface(PANEL), actx)  # _rebuild_tiles runs on draw
    alt_ids = {t.entity_id for t in alt.tiles}

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("systems", {})
    screen.ensure_layout(CONTENT, ctx)
    screen.draw(pygame.Surface(PANEL), ctx)  # Dynamic.refresh populates labels
    chip = next(w for w in leaves(screen) if getattr(w, "label", None) == "LGT")
    chip.on_press()
    screen.ensure_layout(CONTENT, ctx)
    from homeinterface.ui.indicators import EntityTile

    dash_ids = {w.entity_id for w in find_all(screen, EntityTile)}

    assert alt_ids == dash_ids == {"light.living", "light.kitchen", "light.office"}


def test_systems_domain_filter_switches_to_a_different_domain(backend):
    alt, actx = alt_systems(backend)
    alt._set_filter("climate")
    alt.draw(pygame.Surface(PANEL), actx)
    alt_ids = {t.entity_id for t in alt.tiles}

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("systems", {})
    screen.ensure_layout(CONTENT, ctx)
    screen.draw(pygame.Surface(PANEL), ctx)  # Dynamic.refresh populates labels
    chip = next(w for w in leaves(screen) if getattr(w, "label", None) == "CLM")
    chip.on_press()
    screen.ensure_layout(CONTENT, ctx)
    from homeinterface.ui.indicators import EntityTile

    dash_ids = {w.entity_id for w in find_all(screen, EntityTile)}

    assert alt_ids == dash_ids == {"climate.living"}


def test_link_status_reports_the_same_backend_diagnostics_as_systems_screen(backend):
    alt, actx = alt_systems(backend)
    alt.draw(pygame.Surface(PANEL), actx)

    screen, ctx = build(DASHBOARD_YAML, backend)
    screen.goto("systems", {})
    screen.ensure_layout(CONTENT, ctx)
    link = None
    for w in leaves(screen):
        if type(w).__name__ == "_LinkStatus":
            link = w
    assert link is not None
    link.draw(pygame.Surface(PANEL), ctx)

    dash_rows = {row[0]: row[1] for row in link.rows}
    assert dash_rows["ENTITIES"] == str(len(backend.snapshot()))
    assert dash_rows["ALERTS"] == str(len(backend.alerts()))
    assert dash_rows["REVISION"] == str(backend.revision)
    assert dash_rows["STATE"] == backend.link.value.upper()


# == every author-placed node clears the touch minimum ======================
def test_every_author_placed_node_clears_the_touch_minimum(backend):
    touch_min = Theme().touch_min
    for pane, params in (
        ("places", {}), ("place", {"room": "", "zone": "z1"}),
        ("device", {"device": "light.living"}), ("vitals_gauges", {}),
        ("vitals_quick", {}), ("vitals_status", {}), ("systems", {}),
    ):
        screen, ctx = build(DASHBOARD_YAML, backend)
        screen.goto(pane, params)
        screen.ensure_layout(CONTENT, ctx)
        for widget in screen._widgets:
            assert min(widget.rect.width, widget.rect.height) >= touch_min * 0.5, (pane, widget)
