"""The custom shell: laying a dashboard out and driving it.

The panel these guard is 480x320.  Whatever fits there fits everywhere, so
every assertion about size, overlap and paging is made at that resolution -
and the touch minimum is checked in real pixels, because that is what a
fingertip lands on.
"""

from __future__ import annotations

import pygame
import pytest

from homeinterface.app import App
from homeinterface.backend.base import Entity
from homeinterface.backend.mock import MockBackend
from homeinterface.config import DEFAULTS, load_config
from homeinterface.dashboard.build import Count, DashboardScreen
from homeinterface.dashboard.components import (
    ZOOM_STEP,
    DeviceInspector,
    Dynamic,
    FloorPlanView,
    ZoneInspector,
    _lookup,
)
from homeinterface.dashboard.loader import dashboard_from_text
from homeinterface.dashboard.registry import COMPONENTS, builder
from homeinterface.dashboard.schema import Binding, DashboardError
from homeinterface.floorplan.loader import plan_from_dict
from homeinterface.screens.alt import PlaceCard
from homeinterface.fonts import FontBook
from homeinterface.scaling import Viewport
from homeinterface.theme import Theme
from homeinterface.ui.base import UIContext

#: the target panel, and the rectangle the shell leaves a screen on it
PANEL = (480, 320)
CONTENT = pygame.Rect(7, 42, 466, 271)

PLAN = {
    "floors": [{
        "id": "f1",
        "name": "Ground",
        "rooms": [{"id": f"r{i}", "name": f"Room {i}", "rect": [i * 4, 0, 4, 4]} for i in range(4)],
        "devices": [{"entity_id": f"light.r{i}_{d}", "at": [i * 4 + 1 + d, 2], "room": f"r{i}"}
                    for i in range(4) for d in range(3)],
    }],
}


class FakeApp:
    def __init__(self, plan, backend):
        self.theme = Theme()
        self.book = FontBook(self.theme)
        self.plan = plan
        self.backend = backend
        self.config = {}


@pytest.fixture
def backend():
    b = MockBackend(entity_ids=[f"light.r{i}_{d}" for i in range(4) for d in range(3)]
                    + ["sensor.outdoor_temperature", "binary_sensor.porta"], chaos=False)
    b.start()
    yield b
    b.stop()


def build(text: str, backend, size=PANEL, rect=CONTENT):
    plan = plan_from_dict(PLAN)
    app = FakeApp(plan, backend)
    screen = DashboardScreen(app, dashboard_from_text(text, source="dash.yaml"))
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*size), backend=backend)
    screen.ensure_layout(pygame.Rect(rect), ctx)
    return screen, ctx


def leaves(screen: DashboardScreen):
    """Every widget the operator can actually see or hit."""
    out = []
    for widget in screen._widgets:
        inner = getattr(widget, "inner", widget)
        out.append(inner)
        out.extend(getattr(inner, "children", []))
    return out


# -- layout --------------------------------------------------------------
def test_a_component_fills_the_units_it_asked_for(backend):
    screen, _ = build("""
root:
  type: rows
  children:
    - {type: toggle, entity: light.r0_0, columns: 6, rows: 1}
    - {type: toggle, entity: light.r1_0, columns: 6, rows: 2}
""", backend)
    first, second = [w.rect for w in screen._widgets]
    assert first.width == second.width == CONTENT.width
    # a two-row node is twice a one-row node plus the gap between them
    assert second.height == pytest.approx(first.height * 2 + 5, abs=2)
    assert second.bottom <= CONTENT.bottom


def test_nothing_lands_outside_the_rectangle_it_was_given(backend):
    screen, _ = build("""
root:
  type: grid
  children:
    - {type: toggle, entity: light.r0_0, columns: 2, rows: 1}
    - {type: toggle, entity: light.r0_1, columns: 2, rows: 1}
    - {type: toggle, entity: light.r0_2, columns: 2, rows: 1}
    - {type: readout, entity: sensor.outdoor_temperature, columns: 6, rows: 2}
""", backend)
    for widget in screen._widgets:
        assert CONTENT.contains(widget.rect), widget.rect


def test_a_row_of_columns_shares_the_width(backend):
    screen, _ = build("""
root:
  type: cols
  children:
    - {type: toggle, entity: light.r0_0}
    - {type: toggle, entity: light.r0_1}
    - {type: toggle, entity: light.r0_2}
""", backend)
    rects = [w.rect for w in screen._widgets]
    assert len({r.width for r in rects}) == 1
    assert rects[0].height == CONTENT.height


def test_every_target_clears_the_touch_minimum(backend):
    screen, _ = build("""
root:
  type: rows
  children:
    - type: cols
      columns: 6
      rows: 0.5
      children:
        - {type: button, label: A}
        - {type: button, label: B}
    - {type: device-rows, columns: 6, rows: 2.5}
""", backend)
    touch_min = Theme().touch_min
    for widget in screen._widgets:
        # a node the author placed is sized by the grid: half a row is the
        # smallest it can be, and half a row still clears a fingertip
        assert min(widget.rect.width, widget.rect.height) >= touch_min, widget
    for widget in leaves(screen):
        if hasattr(widget, "on_press"):
            assert min(widget.rect.width, widget.rect.height) >= 30, widget


# -- overflow ------------------------------------------------------------
OVERFLOWING = """
root:
  type: rows
  overflow: %s
  children:
    - {type: toggle, entity: light.r0_0, columns: 6, rows: 1}
    - {type: toggle, entity: light.r1_0, columns: 6, rows: 1}
    - {type: toggle, entity: light.r2_0, columns: 6, rows: 1}
    - {type: toggle, entity: light.r3_0, columns: 6, rows: 1}
"""


def test_a_container_that_overflows_gives_up_a_row_to_a_pager(backend):
    screen, ctx = build(OVERFLOWING % "auto", backend)
    pagers = [w for w in screen._widgets if getattr(w, "label", None) in ("<", ">")]
    assert len(pagers) == 2
    assert [w.enabled for w in pagers] == [False, True]
    # the first page holds what fits, and nothing is drawn under the pager
    assert len(screen._widgets) == 2 + 2
    for widget in screen._widgets:
        assert CONTENT.contains(widget.rect)


def test_the_pager_turns_the_page(backend):
    screen, ctx = build(OVERFLOWING % "auto", backend)
    nxt = next(w for w in screen._widgets if getattr(w, "label", None) == ">")
    nxt.on_press()
    screen.ensure_layout(CONTENT, ctx)
    assert screen.pages["0"] == 1
    prev = next(w for w in screen._widgets if getattr(w, "label", None) == "<")
    assert prev.enabled is True


def test_a_container_told_to_clip_says_what_it_is_hiding(backend):
    screen, _ = build(OVERFLOWING % "clip", backend)
    assert not [w for w in screen._widgets if getattr(w, "label", None) in ("<", ">")]
    more = [w for w in screen._widgets if isinstance(w, Count)]
    assert more and more[0].hidden >= 1


# -- data ----------------------------------------------------------------
def test_a_placeholder_reads_this_node_s_own_entity(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - type: readout
      entity: {id: sensor.outdoor_temperature, precision: 1, unit: "°C"}
      label: OUTSIDE
      value: "{state}"
""", backend)
    readout = leaves(screen)[0]
    screen._widgets[0].draw(pygame.Surface(PANEL), ctx)
    assert readout.label == "OUTSIDE"
    assert readout.value.endswith("°C")
    assert "." in readout.value  # precision: 1


def test_a_placeholder_may_name_another_entity_outright(backend):
    binding = Binding("light.r0_0")
    assert _lookup("sensor.outdoor_temperature.state", binding, backend) not in ("", "--")


def test_a_missing_entity_reads_as_inop_not_as_a_crash(backend):
    assert _lookup("state", Binding("light.nonesuch"), backend) == "--"


def test_a_level_map_colours_by_state(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - type: lamp
      entity: binary_sensor.porta
      label: DOOR
      levels:
        - {state: "on", level: caution}
        - {level: normal}
""", backend)
    lamp = leaves(screen)[0]
    surface = pygame.Surface(PANEL)
    for state, expected in (("on", "caution"), ("off", "normal")):
        backend._publish(Entity("binary_sensor.porta", state, {"device_class": "door"}))
        screen._widgets[0].draw(surface, ctx)
        assert lamp.level == expected


def test_visible_if_hides_a_node_from_the_eye_and_from_the_finger(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - type: toggle
      entity: light.nonesuch
      visible_if: {exists: true}
""", backend)
    widget = screen._widgets[0]
    assert isinstance(widget, Dynamic)
    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": widget.rect.center})
    assert widget.handle(down, ctx) is False


def test_repeat_stamps_one_child_per_device(backend):
    screen, _ = build("""
root:
  type: grid
  from: {room: r0}
  template: {type: toggle, entity: $entity, columns: 2, rows: 1}
""", backend)
    entities = {w.inner.entity_id for w in screen._widgets if hasattr(w, "inner")}
    assert entities == {"light.r0_0", "light.r0_1", "light.r0_2"}


def test_every_name_in_the_catalogue_builds_something():
    # a name a dashboard may write with nothing behind it is a boot-time crash
    assert all(builder(name) is not None for name in COMPONENTS)


def test_a_chip_row_makes_its_children_compact(backend):
    screen, _ = build("""
root:
  type: chips
  children:
    - {type: button, label: ALL}
    - {type: button, label: SALA}
""", backend)
    assert all(getattr(w, "inner", w).compact for w in screen._widgets)


# -- navigation ----------------------------------------------------------
NAV = """
start: one
root:
  type: tabs
  children:
    - type: rows
      id: one
      title: ONE
      children:
        - {type: places, columns: 6, rows: 2, on_press: {goto: two, params: {room: $room}}}
    - type: rows
      id: two
      title: TWO
      children:
        - {type: device-rows, room: $room, columns: 6, rows: 2}
"""


def test_tabs_are_a_container_not_chrome(backend):
    screen, _ = build(NAV, backend)
    tabs = [w for w in screen._widgets if getattr(w, "label", None) in ("ONE", "TWO")]
    assert len(tabs) == 2
    assert tabs[0].active and not tabs[1].active
    # the bar sits inside the content rectangle the shell handed over
    assert CONTENT.contains(tabs[0].rect)


def test_goto_selects_the_pane_and_carries_its_params(backend):
    screen, ctx = build(NAV, backend)
    screen.goto("two", {"room": "r1"})
    screen.ensure_layout(CONTENT, ctx)
    assert screen.subtitle == "TWO"
    rows = next(w for w in leaves(screen) if hasattr(w, "devices"))
    assert {d.entity_id for d in rows.devices} == {"light.r1_0", "light.r1_1", "light.r1_2"}


def test_back_returns_to_where_the_goto_started(backend):
    screen, ctx = build(NAV, backend)
    screen.goto("two", {"room": "r1"})
    screen.back()
    screen.ensure_layout(CONTENT, ctx)
    assert screen.subtitle == "ONE"


def test_the_tabs_at_root_are_what_tab_cycles(backend):
    screen, ctx = build(NAV, backend)
    screen.cycle()
    screen.ensure_layout(CONTENT, ctx)
    assert screen.subtitle == "TWO"
    screen.cycle_to(0)
    screen.ensure_layout(CONTENT, ctx)
    assert screen.subtitle == "ONE"


# -- the shell -----------------------------------------------------------
def test_the_shell_is_chosen_by_config_and_defaults_to_stock():
    assert DEFAULTS["ui"]["shell"] == "stock"
    config = load_config(None)
    config["ui"] = {"shell": "custom"}
    config["dashboard"] = "config/dashboard.yaml"
    app = App(config)
    assert app.shell == "custom"
    app._build_screens()
    assert isinstance(app.screen, DashboardScreen)
    # no rail, no tab bar: the dashboard owns its own navigation
    assert app.nav == []


def test_an_unknown_shell_is_refused():
    config = load_config(None)
    config["ui"] = {"shell": "nonesuch"}
    with pytest.raises(ValueError, match="ui.shell"):
        App(config)


def test_a_bad_reload_keeps_the_running_dashboard(tmp_path):
    path = tmp_path / "dash.yaml"
    path.write_text("root: {type: rows, children: [{type: clock}]}", encoding="utf-8")
    config = load_config(None)
    config["ui"] = {"shell": "custom"}
    config["dashboard"] = str(path)
    app = App(config)
    app._build_screens()
    running = app.dashboard

    path.write_text("root: {type: nonesuch}", encoding="utf-8")
    assert app.reload_dashboard() is False
    assert app.dashboard is running
    assert any(a.key == "dashboard.reload" for a in app.backend.alerts())

    path.write_text("root: {type: cols, children: [{type: clock}]}", encoding="utf-8")
    assert app.reload_dashboard() is True
    assert app.dashboard.root.type == "cols"
    assert not [a for a in app.backend.alerts() if a.key == "dashboard.reload"]


def test_a_dashboard_that_will_not_load_stops_the_app(tmp_path):
    config = load_config(None)
    config["ui"] = {"shell": "custom"}
    config["dashboard"] = str(tmp_path / "missing.yaml")
    with pytest.raises(DashboardError):
        App(config)


# -- ADR 0005: shell parity components and repeat sources -----------------
#: a second plan, with two floors and a zone, for floors/rooms/entities repeat
PLAN_MULTI = {
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


@pytest.fixture
def multi_backend():
    ids = ["light.r0_0", "light.r1_0", "light.r2_0", "sensor.outdoor_temperature"]
    b = MockBackend(entity_ids=ids, chaos=False)
    b.start()
    yield b
    b.stop()


def build_multi(text: str, backend, size=PANEL, rect=CONTENT):
    plan = plan_from_dict(PLAN_MULTI)
    app = FakeApp(plan, backend)
    screen = DashboardScreen(app, dashboard_from_text(text, source="dash.yaml"))
    ctx = UIContext(theme=app.theme, book=app.book, vp=Viewport(*size), backend=backend)
    screen.ensure_layout(pygame.Rect(rect), ctx)
    return screen, ctx


# -- label -----------------------------------------------------------------
def test_label_renders_literal_text(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: label, text: "N PLACES"}
""", backend)
    label = leaves(screen)[0]
    screen._widgets[0].draw(pygame.Surface(PANEL), ctx)
    assert label.text == "N PLACES"


def test_label_renders_a_bound_placeholder(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: label, entity: sensor.outdoor_temperature, text: "{state}"}
""", backend)
    label = leaves(screen)[0]
    screen._widgets[0].draw(pygame.Surface(PANEL), ctx)
    assert label.text == backend.get("sensor.outdoor_temperature").state


# -- group-aware toggle / slider -------------------------------------------
def test_a_toggle_with_no_binding_commands_the_entities_list(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: toggle, entities: [light.r0_0, light.r0_1], label: GROUP}
""", backend)
    widget = leaves(screen)[0]
    assert widget.group_ids == ["light.r0_0", "light.r0_1"]
    calls = []
    backend.toggle_group = lambda ids: calls.append(ids)
    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": widget.rect.center})
    up = pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": widget.rect.center})
    screen._widgets[0].handle(down, ctx)
    screen._widgets[0].handle(up, ctx)
    assert calls == [["light.r0_0", "light.r0_1"]]


def test_a_grouped_toggle_reflects_aggregate_state(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: toggle, room: r0, label: ROOM 0}
""", backend)
    widget = leaves(screen)[0]
    surface = pygame.Surface(PANEL)
    for eid in ("light.r0_0", "light.r0_1", "light.r0_2"):
        backend._publish(Entity(eid, "off"))
    screen._widgets[0].draw(surface, ctx)
    assert widget.active is False
    backend._publish(Entity("light.r0_0", "on"))
    screen._widgets[0].draw(surface, ctx)
    assert widget.active is True
    assert widget.sub == "1/3"


def test_a_grouped_slider_commands_the_group(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: slider, room: r0, control: brightness}
""", backend)
    widget = leaves(screen)[0]
    calls = []
    backend.set_group_brightness = lambda ids, v: calls.append((ids, v))
    widget.on_commit(42)
    assert calls == [(["light.r0_0", "light.r0_1", "light.r0_2"], 42)]


def test_a_single_entity_slider_is_unaffected_by_grouping(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: slider, entity: light.r0_0, control: brightness}
""", backend)
    widget = leaves(screen)[0]
    calls = []
    backend.set_brightness = lambda eid, v: calls.append((eid, v))
    widget.on_commit(10)
    assert calls == [("light.r0_0", 10)]


# -- repeat.over: floors / rooms / entities --------------------------------
def test_repeat_over_floors_stamps_one_child_per_floor(multi_backend):
    screen, ctx = build_multi("""
root:
  type: rows
  over: floors
  from: {}
  template: {type: label, text: "$name", columns: 6, rows: 1}
""", multi_backend)
    surface = pygame.Surface(PANEL)
    texts = []
    for widget in screen._widgets:
        widget.draw(surface, ctx)
        texts.append(widget.inner.text)
    assert texts == ["Ground", "Upper"]


def test_repeat_over_rooms_carries_the_zone_in_scope(multi_backend):
    screen, ctx = build_multi("""
root:
  type: rows
  over: rooms
  from: {}
  template: {type: label, text: "$room:$zone", columns: 6, rows: 1}
""", multi_backend)
    surface = pygame.Surface(PANEL)
    texts = set()
    for widget in screen._widgets:
        widget.draw(surface, ctx)
        texts.add(widget.inner.text)
    assert texts == {"r0:z1", "r1:z1", "r2:"}


def test_repeat_over_rooms_respects_the_zone_selector(multi_backend):
    screen, ctx = build_multi("""
root:
  type: rows
  over: rooms
  from: {zone: z1}
  template: {type: label, text: "$room", columns: 6, rows: 1}
""", multi_backend)
    assert len(screen._widgets) == 2


def test_repeat_over_entities_takes_a_literal_list(backend):
    screen, _ = build("""
root:
  type: rows
  over: entities
  from: {entities: [light.r0_0, light.r0_1]}
  template: {type: toggle, entity: $entity, columns: 6, rows: 1}
""", backend)
    ids = {w.inner.entity_id for w in screen._widgets if hasattr(w, "inner")}
    assert ids == {"light.r0_0", "light.r0_1"}


def test_repeat_over_entities_scans_the_backend_by_domain(backend):
    screen, _ = build("""
root:
  type: grid
  over: entities
  from: {domain: sensor}
  template: {type: toggle, entity: $entity, columns: 2, rows: 1}
""", backend)
    ids = {w.inner.entity_id for w in screen._widgets if hasattr(w, "inner")}
    expected = {eid for eid in backend.snapshot() if eid.startswith("sensor.")}
    assert ids == expected


# -- attr-list / link-status ------------------------------------------------
def test_attr_list_prints_state_and_present_attributes(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: attr-list, entity: light.r0_0}
""", backend)
    widget = leaves(screen)[0]
    backend._publish(Entity("light.r0_0", "on", {"brightness": 128, "unrelated": "x"}))
    screen._widgets[0].draw(pygame.Surface(PANEL), ctx)
    assert widget.lines[0][0] == "STATE  ON"
    assert widget.lines[1][0].startswith("BRIGHTNESS")
    assert "128" in widget.lines[1][0]
    assert len(widget.lines) == 2  # unrelated: not in the whitelist


def test_attr_list_flags_an_entity_missing_from_the_backend(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: attr-list, entity: light.nonesuch}
""", backend)
    widget = leaves(screen)[0]
    screen._widgets[0].draw(pygame.Surface(PANEL), ctx)
    assert widget.lines == [("ENTITY NOT IN BACKEND", ctx.theme.caution)]


def test_link_status_reports_backend_diagnostics(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: link-status}
""", backend)
    widget = leaves(screen)[0]
    screen._widgets[0].draw(pygame.Surface(PANEL), ctx)
    labels = [row[0] for row in widget.rows]
    assert labels == ["STATE", "BACKEND", "ENTITIES", "ALERTS", "REVISION"]
    entities_row = next(r for r in widget.rows if r[0] == "ENTITIES")
    assert entities_row[1] == str(len(backend.snapshot()))


# -- tabs.bar: left | right -------------------------------------------------
def test_tabs_bar_left_makes_a_vertical_rail(backend):
    screen, _ = build("""
root:
  type: tabs
  bar: left
  children:
    - {type: rows, id: one, title: ONE, children: [{type: clock}]}
    - {type: rows, id: two, title: TWO, children: [{type: clock}]}
""", backend)
    tabs = [w for w in screen._widgets if getattr(w, "label", None) in ("ONE", "TWO")]
    assert len(tabs) == 2
    one, two = tabs
    # a side rail stacks tabs vertically and narrows the content width
    assert one.rect.left == two.rect.left
    assert one.rect.top < two.rect.top
    assert one.rect.right <= CONTENT.left + CONTENT.width * 0.4


def test_tabs_bar_right_puts_the_rail_on_the_right(backend):
    screen, _ = build("""
root:
  type: tabs
  bar: right
  children:
    - {type: rows, id: one, title: ONE, children: [{type: clock}]}
    - {type: rows, id: two, title: TWO, children: [{type: clock}]}
""", backend)
    tabs = [w for w in screen._widgets if getattr(w, "label", None) in ("ONE", "TWO")]
    assert tabs[0].rect.right >= CONTENT.right - CONTENT.width * 0.4


# -- places sub-label (area) -------------------------------------------------
# -- ADR 0006: floorplan zoom, pan and the focus: prop ----------------------
def _floorplan_widget(screen: DashboardScreen) -> FloorPlanView:
    for widget in leaves(screen):
        if isinstance(widget, FloorPlanView):
            return widget
    raise AssertionError("no FloorPlanView in this screen")


def test_zoom_buttons_change_the_zoom(backend):
    screen, ctx = build("""
root:
  type: floorplan
  floor: f1
""", backend)
    fp = _floorplan_widget(screen)
    assert fp.zoom == pytest.approx(1.0)
    fp.btn_zoom_in.on_press()
    assert fp.zoom == pytest.approx(ZOOM_STEP)
    fp.btn_zoom_out.on_press()
    assert fp.zoom == pytest.approx(1.0)
    fp.btn_zoom_in.on_press()
    fp.pan = (2.0, 3.0)
    fp.btn_reset.on_press()
    assert fp.zoom == pytest.approx(1.0)
    assert fp.pan == (0.0, 0.0)


def test_wheel_zooms_the_view(backend):
    screen, ctx = build("""
root:
  type: floorplan
  floor: f1
""", backend)
    fp = _floorplan_widget(screen)
    ctx.pointer = fp.rect.center
    before = fp.zoom
    wheel_in = pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": 1})
    assert fp.handle(wheel_in, ctx) is True
    assert fp.zoom == pytest.approx(before * ZOOM_STEP)
    wheel_out = pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": -1})
    assert fp.handle(wheel_out, ctx) is True
    assert fp.zoom == pytest.approx(before)


def test_right_drag_pans_the_view(backend):
    screen, ctx = build("""
root:
  type: floorplan
  floor: f1
""", backend)
    fp = _floorplan_widget(screen)
    start = fp.rect.center
    end = (start[0] + 24, start[1] + 12)
    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 3, "pos": start})
    move = pygame.event.Event(pygame.MOUSEMOTION, {"pos": end})
    up = pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 3, "pos": end})
    assert fp.handle(down, ctx) is True
    assert fp.handle(move, ctx) is True
    assert fp.pan != (0.0, 0.0)
    assert fp.handle(up, ctx) is True
    assert fp._panning is False


# a sibling tab exists purely so switching to it and back forces the screen
# to rebuild - a fresh FloorPlanView instance - which is exactly the
# situation the persisted view-state dict has to survive (docs/adr/0006)
ZOOM_PAN_DASH = """
root:
  type: tabs
  children:
    - type: rows
      id: one
      title: ONE
      children:
        - {type: floorplan, floor: f1, columns: 6, rows: 3}
    - type: rows
      id: two
      title: TWO
      children:
        - {type: clock, columns: 6, rows: 3}
"""


def test_zoom_and_pan_survive_an_unrelated_invalidate(backend):
    screen, ctx = build(ZOOM_PAN_DASH, backend)
    fp = _floorplan_widget(screen)
    fp.btn_zoom_in.on_press()
    fp.btn_zoom_in.on_press()
    zoomed = fp.zoom
    fp.pan = (5.0, -3.0)
    panned = fp.pan
    assert zoomed != pytest.approx(1.0)

    # switching away and back rebuilds the widget tree - a fresh
    # FloorPlanView is constructed for "one" both times
    screen.cycle()
    screen.ensure_layout(CONTENT, ctx)
    screen.cycle()
    screen.ensure_layout(CONTENT, ctx)

    fp2 = _floorplan_widget(screen)
    assert fp2 is not fp
    assert fp2.zoom == pytest.approx(zoomed)
    assert fp2.pan == panned


# -- focus: prop --------------------------------------------------------
def test_no_focus_prop_is_the_overview_stage(multi_backend):
    screen, ctx = build_multi("""
root:
  type: floorplan
  floor: f1
""", multi_backend)
    fp = _floorplan_widget(screen)
    assert fp.focused is False
    assert fp.focus_rooms == frozenset()


def test_focus_prop_restricts_to_one_unzoned_room(multi_backend):
    screen, ctx = build_multi("""
root:
  type: floorplan
  floor: f2
  focus: r2
""", multi_backend)
    fp = _floorplan_widget(screen)
    assert fp.focused is True
    assert fp.focus_rooms == frozenset({"r2"})
    assert {d.entity_id for d in fp._focus_devices()} == {"light.r2_0"}


def test_focus_prop_on_a_zoned_room_shows_the_whole_zone(multi_backend):
    # r0 belongs to zone z1 together with r1 (PLAN_MULTI) - focusing r0 must
    # pull in r1 too, mirroring PlanScreen._enter_focus's zone lookup
    screen, ctx = build_multi("""
root:
  type: floorplan
  floor: f1
  focus: r0
""", multi_backend)
    fp = _floorplan_widget(screen)
    assert fp.focus_rooms == frozenset({"r0", "r1"})
    assert {d.entity_id for d in fp._focus_devices()} == {"light.r0_0", "light.r1_0"}


def test_places_grid_shows_the_area_as_the_sub_label(backend):
    screen, _ = build("""
root:
  type: rows
  children:
    - {type: places, floor: f1}
""", backend)
    grid = leaves(screen)[0]
    cards = [w for w in grid.children if isinstance(w, PlaceCard)]
    assert cards
    for card in cards:
        assert card.sub.endswith(" m²")
        assert card.sub != " m²"


# -- ADR 0006: the `set` action -----------------------------------------
def test_set_action_writes_a_param_without_navigating(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: button, label: PICK, on_press: {set: device, value: light.r0_0}}
""", backend)
    before = dict(screen.selected)
    button = leaves(screen)[0]
    button.on_press()
    assert screen.params.get("device") == "light.r0_0"
    assert screen.selected == before
    assert screen._history == []


def test_set_action_expands_a_param_template(backend):
    screen, ctx = build("""
root:
  type: rows
  children:
    - {type: button, label: PICK, on_press: {set: device, value: $entity}}
""", backend)
    button = leaves(screen)[0]
    screen.run_action(screen.dashboard.root.children[0].action, {"entity": "light.r2_0"}, {})
    assert screen.params["device"] == "light.r2_0"


# -- ADR 0006: floorplan on_select wires a device tap to `set` -----------
def test_floorplan_on_select_fires_set_on_a_focused_device_tap(multi_backend):
    screen, ctx = build_multi("""
root:
  type: floorplan
  floor: f2
  focus: r2
  on_select: {set: device, value: $entity}
""", multi_backend)
    fp = _floorplan_widget(screen)
    view = fp._current_view()
    device = next(d for d in fp._focus_devices() if d.entity_id == "light.r2_0")
    pos = view.to_screen(device.at)
    tap = pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": pos})
    assert fp.handle(tap, ctx) is True
    assert screen.params.get("device") == "light.r2_0"


def test_floorplan_device_tap_without_on_select_does_nothing(multi_backend):
    screen, ctx = build_multi("""
root:
  type: floorplan
  floor: f2
  focus: r2
""", multi_backend)
    fp = _floorplan_widget(screen)
    view = fp._current_view()
    device = next(d for d in fp._focus_devices() if d.entity_id == "light.r2_0")
    pos = view.to_screen(device.at)
    tap = pygame.event.Event(pygame.MOUSEBUTTONUP, {"button": 1, "pos": pos})
    assert fp.handle(tap, ctx) is True
    assert "device" not in screen.params


# -- ADR 0006: device-inspector -------------------------------------------
@pytest.fixture
def kinds_backend():
    ids = ["light.lamp", "climate.thermo", "cover.blind"]
    b = MockBackend(entity_ids=ids, chaos=False)
    b.start()
    yield b
    b.stop()


def _device_inspector_widget(screen: DashboardScreen) -> DeviceInspector:
    for widget in leaves(screen):
        if isinstance(widget, DeviceInspector):
            return widget
    raise AssertionError("no DeviceInspector in this screen")


def test_device_inspector_shows_a_toggle_for_the_bound_entity(kinds_backend):
    screen, ctx = build("""
root:
  type: device-inspector
  entity: light.lamp
""", kinds_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _device_inspector_widget(screen)
    assert inspector.entity_id == "light.lamp"
    assert inspector.toggle.sub == "light.lamp"


def test_device_inspector_brightness_slider_for_a_light(kinds_backend):
    screen, ctx = build("""
root:
  type: device-inspector
  entity: light.lamp
""", kinds_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _device_inspector_widget(screen)
    assert inspector.slider is not None
    assert inspector.slider.label == "BRIGHTNESS"


def test_device_inspector_temperature_slider_for_climate(kinds_backend):
    screen, ctx = build("""
root:
  type: device-inspector
  entity: climate.thermo
""", kinds_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _device_inspector_widget(screen)
    assert inspector.slider is not None
    assert inspector.slider.label == "TARGET"


def test_device_inspector_position_slider_for_a_cover(kinds_backend):
    screen, ctx = build("""
root:
  type: device-inspector
  entity: cover.blind
""", kinds_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _device_inspector_widget(screen)
    assert inspector.slider is not None
    assert inspector.slider.label == "POSITION"


def test_device_inspector_no_slider_for_a_sensor(backend):
    screen, ctx = build("""
root:
  type: device-inspector
  entity: sensor.outdoor_temperature
""", backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _device_inspector_widget(screen)
    assert inspector.slider is None


def test_device_inspector_toggle_press_calls_backend_toggle(kinds_backend, monkeypatch):
    screen, ctx = build("""
root:
  type: device-inspector
  entity: light.lamp
""", kinds_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _device_inspector_widget(screen)
    calls = []
    monkeypatch.setattr(kinds_backend, "toggle", lambda eid: calls.append(eid))
    inspector.toggle.on_press()
    assert calls == ["light.lamp"]


def test_device_inspector_visible_if_exists_hides_when_unset(backend):
    screen, ctx = build("""
root:
  type: device-inspector
  entity: $device
  visible_if: {exists: true}
""", backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    widget = screen._widgets[0]
    surface = pygame.Surface((480, 320))
    assert isinstance(widget, Dynamic)
    assert widget._shown(ctx) is False


# -- ADR 0006: zone-inspector -----------------------------------------------
def _zone_inspector_widget(screen: DashboardScreen) -> ZoneInspector:
    for widget in leaves(screen):
        if isinstance(widget, ZoneInspector):
            return widget
    raise AssertionError("no ZoneInspector in this screen")


def test_zone_inspector_master_toggle_reflects_aggregate_state(multi_backend):
    screen, ctx = build_multi("""
root:
  type: zone-inspector
  zone: z1
""", multi_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _zone_inspector_widget(screen)
    inspector.draw(pygame.Surface((480, 320)), ctx)
    on, total = multi_backend.group_state(["light.r0_0", "light.r1_0"])
    assert inspector.master.active == (on > 0)
    assert inspector.master.sub == (f"{on}/{total} ON" if total else "NO DEVICES")


def test_zone_inspector_brightness_slider_iff_a_light_is_in_scope(multi_backend):
    screen, ctx = build_multi("""
root:
  type: zone-inspector
  zone: z1
""", multi_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _zone_inspector_widget(screen)
    assert inspector.brightness is not None  # z1 has light.r0_0, light.r1_0


def test_zone_inspector_temperature_slider_iff_a_climate_entity_is_in_scope(multi_backend):
    screen, ctx = build_multi("""
root:
  type: zone-inspector
  zone: z1
""", multi_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _zone_inspector_widget(screen)
    assert inspector.temperature is None  # z1 has no climate.* entities


def test_zone_inspector_resolves_a_room_to_its_zone(multi_backend):
    # $room -> r0 -> zone z1 (PLAN_MULTI): the room-without-a-known-floor
    # case is resolved by searching every floor for the room id
    screen, ctx = build_multi("""
root:
  type: zone-inspector
  room: r0
""", multi_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _zone_inspector_widget(screen)
    assert set(inspector.entity_ids) == {"light.r0_0", "light.r1_0"}


def test_zone_inspector_falls_back_to_a_plain_room_when_unzoned(multi_backend):
    # r2 (floor f2) has no zone in PLAN_MULTI
    screen, ctx = build_multi("""
root:
  type: zone-inspector
  room: r2
""", multi_backend, size=(480, 320), rect=pygame.Rect(0, 0, 480, 320))
    inspector = _zone_inspector_widget(screen)
    assert set(inspector.entity_ids) == {"light.r2_0"}
