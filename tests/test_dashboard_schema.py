"""Parsing and validating a dashboard file.

What these guard is the promise the custom shell makes to someone editing YAML
over ssh on a panel with no keyboard: every mistake we *can* catch is caught
before the app comes up, and the message says which line to fix.  Nothing here
needs a display, which is the other half of the promise - see docs/adr/0002.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homeinterface.dashboard.loader import dashboard_from_text, load_dashboard
from homeinterface.dashboard.schema import DashboardError, child_span

#: the shipped example, which has to stay loadable
EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "dashboard.yaml"

MINIMAL = """
root:
  type: rows
  children:
    - {type: toggle, entity: light.sala}
"""


def load(text: str):
    return dashboard_from_text(text, source="dash.yaml")


def test_the_shipped_example_loads():
    dashboard = load_dashboard(EXAMPLE)
    assert dashboard.start == "home"
    assert dashboard.node("home") is not None


def test_a_node_tree_has_one_root_and_no_screen_list():
    dashboard = load(MINIMAL)
    assert dashboard.root.type == "rows"
    assert dashboard.root.children[0].binding.entity == "light.sala"


def test_the_schema_never_reaches_for_a_display():
    # the loader has to run in CI and in tools, neither of which has a panel
    for name in ("schema.py", "loader.py"):
        source = Path(__file__).resolve().parents[1] / "homeinterface" / "dashboard" / name
        lines = source.read_text(encoding="utf-8").splitlines()
        assert not [line for line in lines if line.startswith(("import ", "from ")) and "pygame" in line]


# -- refusals ------------------------------------------------------------
def test_an_unknown_type_names_its_line():
    with pytest.raises(DashboardError) as exc:
        load("""
root:
  type: rows
  children:
    - {type: nonesuch}
""")
    assert "nonesuch" in str(exc.value)
    assert str(exc.value).startswith("dash.yaml:5:")


def test_half_a_row_is_allowed_and_half_a_column_is_not():
    load("""
root:
  type: rows
  children:
    - {type: toggle, columns: 6, rows: 0.5}
""")
    with pytest.raises(DashboardError, match="columns must be in whole units"):
        load("""
root:
  type: rows
  children:
    - {type: toggle, columns: 1.5, rows: 1}
""")


def test_a_child_may_not_be_bigger_than_its_parent():
    with pytest.raises(DashboardError, match="asks for 4 columns inside a 2-column"):
        load("""
root:
  type: rows
  children:
    - type: cols
      columns: 2
      rows: 1
      children:
        - {type: toggle, columns: 4, rows: 1}
""")


def test_a_goto_that_names_nothing_is_refused():
    with pytest.raises(DashboardError, match="names no node"):
        load("""
root:
  type: rows
  children:
    - {type: button, label: GO, on_press: {goto: nowhere}}
""")


def test_a_start_that_names_nothing_is_refused():
    with pytest.raises(DashboardError, match="start 'nowhere'"):
        load("start: nowhere\n" + MINIMAL)


def test_ids_are_unique():
    with pytest.raises(DashboardError, match="duplicate id 'twice'"):
        load("""
root:
  type: rows
  children:
    - {type: toggle, id: twice}
    - {type: toggle, id: twice}
""")


def test_a_component_may_not_hold_children():
    with pytest.raises(DashboardError, match="not a container"):
        load("""
root:
  type: rows
  children:
    - type: toggle
      children:
        - {type: toggle}
""")


def test_a_condition_asks_exactly_one_question():
    with pytest.raises(DashboardError, match="exactly one"):
        load("""
root:
  type: rows
  children:
    - {type: toggle, entity: light.sala, visible_if: {state: "on", above: 3}}
""")


def test_the_fallback_level_comes_last():
    with pytest.raises(DashboardError, match="must come last"):
        load("""
root:
  type: rows
  children:
    - type: lamp
      entity: binary_sensor.porta
      levels:
        - {level: normal}
        - {state: "on", level: caution}
""")


def test_a_template_needs_a_selector_to_repeat_over():
    with pytest.raises(DashboardError, match="needs a from"):
        load("""
root:
  type: rows
  template: {type: toggle}
""")


def test_a_selector_matches_places_not_arbitrary_keys():
    with pytest.raises(DashboardError, match="selector matches room, zone, floor or kind"):
        load("""
root:
  type: rows
  from: {colour: blue}
  template: {type: toggle}
""")


# -- span defaulting -----------------------------------------------------
def test_a_container_fills_in_the_spans_its_author_left_out():
    dashboard = load("""
root:
  type: cols
  children:
    - {type: toggle}
    - {type: toggle}
""")
    child = dashboard.root.children[0]
    assert child_span("cols", child, 2, 6.0, 3.0) == (3.0, 3.0)
    assert child_span("rows", child, 2, 6.0, 3.0) == (6.0, 1.0)
    assert child_span("grid", child, 2, 6.0, 3.0) == (1.0, 1.0)


def test_an_explicit_span_is_clamped_to_the_room_there_is():
    # a pane is shorter than the screen by whatever its tab bar took
    dashboard = load("""
root:
  type: rows
  children:
    - {type: toggle, columns: 6, rows: 3}
""")
    child = dashboard.root.children[0]
    assert child_span("rows", child, 1, 6.0, 2.5) == (6.0, 2.5)
