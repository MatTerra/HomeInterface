"""Dashboard document model and validation.

A dashboard file is one tree of :class:`Node`.  This module is the tree plus
the rules a tree has to obey; it never imports pygame, so a dashboard can be
parsed, validated and diffed on a machine with no display - which is what
keeps the layout tests cheap.

Everything an author writes lands in one of a small number of shapes:
:class:`Span` (how much room a node takes), :class:`Binding` (which entity it
reads), :class:`Predicate` (one yes/no question about an entity),
:class:`LevelRule` (a predicate paired with a colour), :class:`Repeat` (one
child per match against the plan) and :class:`Action` (what a tap does).
There is no expression language - see docs/adr/0002.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterator

#: The author-facing grid: a screen is six units across and three down.  The
#: internal cell grid is twice as fine on both axes (docs/adr/0001), which is
#: where the half-unit row comes from.
ROOT_COLUMNS = 6.0
ROOT_ROWS = 3.0
#: Rows may be asked for in halves, columns may not: half a unit is 37.6
#: design units tall (clears a device row) but only 38.8 wide (under the
#: theme's 40-unit touch minimum).
ROW_STEP = 0.5
COLUMN_STEP = 1.0

#: Predicate forms.  ``state`` compares for equality, ``above``/``below``
#: compare numerically, ``exists`` asks whether the entity is present at all.
PREDICATE_KINDS = ("state", "above", "below", "exists")
#: Theme roles a level map may name; the theme resolves these to colours.
LEVELS = ("normal", "ok", "on", "info", "caution", "warning", "inop", "off")
ACTION_KINDS = ("toggle", "goto", "back", "call", "none")
#: What a container does when it holds more than fits: turn pages, or clip.
OVERFLOW_MODES = ("auto", "clip")
#: A container needs a second row before it can give one up to a pager.
PAGER_MIN_ROWS = 2.0


class DashboardError(Exception):
    """A dashboard that cannot be trusted to render.

    Carries the file and line so the message points at the author's mistake
    rather than at our parser.
    """

    def __init__(self, message: str, *, source: str | None = None, line: int | None = None):
        self.message = message
        self.source = source
        self.line = line
        super().__init__(str(self))

    def __str__(self) -> str:
        where = self.source or "<dashboard>"
        if self.line is not None:
            where = f"{where}:{self.line}"
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class Span:
    """How many author units a node occupies, across and down.

    Units are absolute: one unit is the same size wherever it appears, so a
    control that clears the touch minimum at the root still clears it nested
    three deep.  A container's span is therefore also the size of the grid its
    children are placed on.
    """

    columns: float = 1.0
    rows: float = 1.0

    def validate(self, *, source: str | None = None, line: int | None = None) -> None:
        for name, value, step in (("columns", self.columns, COLUMN_STEP),
                                  ("rows", self.rows, ROW_STEP)):
            if value <= 0:
                raise DashboardError(f"{name} must be positive, got {value:g}",
                                     source=source, line=line)
            if abs(value / step - round(value / step)) > 1e-6:
                unit = "whole units" if step == 1.0 else "halves of a unit"
                raise DashboardError(f"{name} must be in {unit}, got {value:g}",
                                     source=source, line=line)


@dataclass(frozen=True)
class Binding:
    """The entity a component reads, and how its value prints.

    Formatting lives here rather than in the placeholder because placeholders
    do lookups only: ``precision`` and ``unit`` are the whole of the
    formatting vocabulary.
    """

    entity: str
    precision: int | None = None
    unit: str | None = None


@dataclass(frozen=True)
class Predicate:
    """One yes/no question about one entity.

    ``entity`` is None when the question is about the node's own binding,
    which is the usual case; naming an entity here asks about another one.
    """

    kind: str
    value: Any = None
    entity: str | None = None
    attribute: str = "state"

    def validate(self, *, source: str | None = None, line: int | None = None) -> None:
        if self.kind not in PREDICATE_KINDS:
            raise DashboardError(
                f"unknown condition {self.kind!r} (expected one of {', '.join(PREDICATE_KINDS)})",
                source=source, line=line,
            )
        if self.kind in ("above", "below"):
            try:
                float(self.value)
            except (TypeError, ValueError):
                raise DashboardError(f"{self.kind} needs a number, got {self.value!r}",
                                     source=source, line=line) from None


@dataclass(frozen=True)
class LevelRule:
    """A predicate paired with the theme role to draw in when it holds.

    A rule with no predicate is the fallback and must come last.
    """

    level: str
    predicate: Predicate | None = None

    def validate(self, *, source: str | None = None, line: int | None = None) -> None:
        if self.level not in LEVELS:
            raise DashboardError(
                f"unknown level {self.level!r} (expected one of {', '.join(LEVELS)})",
                source=source, line=line,
            )
        if self.predicate is not None:
            self.predicate.validate(source=source, line=line)


@dataclass(frozen=True)
class Selector:
    """Which places or devices something applies to, described not listed.

    Every field given has to match, so a selector reads as an AND.  ``kind``
    matches the device's resolved kind (light, climate, cover, sensor).
    """

    room: str | None = None
    zone: str | None = None
    floor: str | None = None
    kind: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.room, self.zone, self.floor, self.kind))


@dataclass(frozen=True)
class Repeat:
    """One child per match: a selector over the plan and the shape to stamp."""

    selector: Selector
    template: "Node"
    #: what the selector enumerates: devices, or places (zones + lone rooms)
    over: str = "devices"


@dataclass(frozen=True)
class Action:
    """What a tap does.  ``params`` values may be ``$name`` references."""

    kind: str
    target: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    service: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def validate(self, *, source: str | None = None, line: int | None = None) -> None:
        if self.kind not in ACTION_KINDS:
            raise DashboardError(
                f"unknown action {self.kind!r} (expected one of {', '.join(ACTION_KINDS)})",
                source=source, line=line,
            )
        if self.kind == "goto" and not self.target:
            raise DashboardError("goto needs the id of a node to go to", source=source, line=line)
        if self.kind == "call" and (not self.service or "." not in str(self.service)):
            raise DashboardError(f"call needs a domain.service, got {self.service!r}",
                                 source=source, line=line)


@dataclass
class Node:
    """One entry in the tree: a container that holds nodes, or a component."""

    type: str
    id: str | None = None
    span: Span = field(default_factory=Span)
    props: dict[str, Any] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    binding: Binding | None = None
    visible_if: Predicate | None = None
    levels: tuple[LevelRule, ...] = ()
    repeat: Repeat | None = None
    overflow: str = "auto"
    action: Action | None = None
    #: True when the author wrote columns:/rows: - an implicit span is filled
    #: in by the container, which knows whether a child should stretch
    has_span: bool = False
    line: int | None = None

    def walk(self) -> Iterator["Node"]:
        """This node, then every descendant, repeat templates included."""
        yield self
        for child in self.children:
            yield from child.walk()
        if self.repeat is not None:
            yield from self.repeat.template.walk()

    def with_span(self, span: Span) -> "Node":
        return replace(self, span=span)


@dataclass
class Dashboard:
    """A parsed dashboard file: one tree, and where navigation starts."""

    root: Node
    start: str | None = None
    source: str | None = None

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.root.walk() if n.id == node_id), None)


def child_span(container_type: str, child: "Node", siblings: int,
               columns: float, rows: float) -> tuple[float, float]:
    """The span a child ends up with inside a container of this shape.

    An author who writes no ``columns:``/``rows:`` is handing the decision to
    the container: a row stretches across, a column takes an equal share, a
    grid cell is one unit.  The layout engine and the validator have to agree
    on this, so it is written once, here.
    """
    if child.has_span:
        # clamped, not refused: a pane is smaller than the screen by whatever
        # its tab bar took, and "fill the pane" is what the author meant
        return min(child.span.columns, columns), min(child.span.rows, rows)
    if container_type == "rows":
        return columns, min(1.0, rows)
    if container_type in ("cols", "chips"):
        return columns / max(siblings, 1), rows
    if container_type == "tabs":
        return columns, rows
    return 1.0, 1.0


def validate(dashboard: Dashboard, known_types: set[str], containers: set[str]) -> None:
    """Refuse a dashboard we could not render honestly.

    Everything checkable without a display is checked here and at load time,
    on the principle that a panel with no keyboard must never come up showing
    a black rectangle where a screen was meant to be.
    """
    source = dashboard.source
    seen: dict[str, Node] = {}
    _check(dashboard.root, ROOT_COLUMNS, ROOT_ROWS, known_types, containers, seen, source)

    for node in dashboard.root.walk():
        action = node.action
        if action is not None and action.kind == "goto" and action.target not in seen:
            raise DashboardError(f"goto {action.target!r} names no node in this dashboard",
                                 source=source, line=node.line)
    if dashboard.start and dashboard.start not in seen:
        raise DashboardError(f"start {dashboard.start!r} names no node in this dashboard",
                             source=source)


def _check(node: Node, columns: float, rows: float, known_types: set[str],
           containers: set[str], seen: dict[str, Node], source: str | None) -> None:
    """One node against the room it was given, then its children against it."""
    if node.type not in known_types:
        raise DashboardError(f"unknown component {node.type!r}", source=source, line=node.line)
    if node.children and node.type not in containers:
        raise DashboardError(f"{node.type!r} is not a container but has children",
                             source=source, line=node.line)
    if node.repeat is not None and node.type not in containers:
        raise DashboardError(f"{node.type!r} is not a container but repeats",
                             source=source, line=node.line)
    if node.overflow not in OVERFLOW_MODES:
        raise DashboardError(
            f"overflow must be one of {', '.join(OVERFLOW_MODES)}, got {node.overflow!r}",
            source=source, line=node.line,
        )
    node.span.validate(source=source, line=node.line)
    if node.visible_if is not None:
        node.visible_if.validate(source=source, line=node.line)
    for rule in node.levels:
        rule.validate(source=source, line=node.line)
    _validate_levels(node, source)
    if node.action is not None:
        node.action.validate(source=source, line=node.line)
    if node.id:
        if node.id in seen:
            raise DashboardError(f"duplicate id {node.id!r}", source=source, line=node.line)
        seen[node.id] = node

    children = list(node.children)
    if node.repeat is not None:
        children.append(node.repeat.template)
    if not children or node.type not in containers:
        return
    for child in children:
        _fits_in(child, node, columns, rows, source)
        span_c, span_r = child_span(node.type, child, len(children), columns, rows)
        _check(child, span_c, span_r, known_types, containers, seen, source)


def _validate_levels(node: Node, source: str | None) -> None:
    for index, rule in enumerate(node.levels):
        if rule.predicate is None and index != len(node.levels) - 1:
            raise DashboardError(
                "the level with no condition is the fallback and must come last",
                source=source, line=node.line,
            )


def _fits_in(child: Node, parent: Node, columns: float, rows: float,
             source: str | None) -> None:
    """A child may never be asked to be bigger than the box it sits in."""
    if not child.has_span or parent.type == "tabs":
        # panes fill whatever the tab bar leaves; implicit spans are the
        # container's decision, and a container never overfills itself
        return
    if child.span.columns > columns + 1e-6:
        raise DashboardError(
            f"{child.type!r} asks for {child.span.columns:g} columns inside a"
            f" {columns:g}-column {parent.type!r}",
            source=source, line=child.line,
        )
    if child.span.rows > rows + 1e-6:
        raise DashboardError(
            f"{child.type!r} asks for {child.span.rows:g} rows inside a"
            f" {rows:g}-row {parent.type!r}",
            source=source, line=child.line,
        )
