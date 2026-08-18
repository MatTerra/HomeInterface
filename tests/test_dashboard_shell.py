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
from homeinterface.dashboard.components import Dynamic, _lookup
from homeinterface.dashboard.loader import dashboard_from_text
from homeinterface.dashboard.registry import COMPONENTS, builder
from homeinterface.dashboard.schema import Binding, DashboardError
from homeinterface.floorplan.loader import plan_from_dict
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
