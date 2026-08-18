"""Turn a dashboard tree into laid-out widgets, and drive it.

The layout engine places nodes on the author grid: one unit is a fixed size
derived from the content rectangle (six units across, three down), so a
control that clears the touch minimum at the root still clears it nested three
deep.  Containers flow their children row-major; a container that holds more
than fits gives up one of its own rows to a pager (docs/adr/0001).

:class:`DashboardScreen` is the shell side: it owns navigation state (which
pane of which tabs is showing, which page of which container, and the
parameters a ``goto`` carried) and rebuilds the widget list whenever that
state or the rectangle changes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pygame

from ..fonts import blit_text
from ..scaling import Box
from ..screens.base import Screen
from ..ui.base import UIContext, Widget
from ..ui.controls import Button
from . import components as _components  # noqa: F401  (registers the built-ins)
from .components import BuildContext, select_devices, select_places
from .registry import CONTAINERS, builder
from .schema import (
    PAGER_MIN_ROWS,
    ROOT_COLUMNS,
    ROOT_ROWS,
    Action,
    Dashboard,
    DashboardError,
    Node,
    child_span,
)

#: A tab bar is half a unit of its container's grid, so the panes below it get
#: a whole number of half-units rather than whatever was left over.
TABS_ROWS = 0.5
MIN_TABS_PX = 40
#: A pager takes one of its container's rows (docs/adr/0001) - never less than
#: a fingertip, however small the container's rows are.
PAGER_ROWS = 1.0
MIN_PAGER_PX = 34
#: floating-point slack when comparing spans
EPS = 1e-6


class Placed:
    """One child, positioned on its container's grid, in units."""

    __slots__ = ("node", "column", "row", "columns", "rows", "key", "scope")

    def __init__(self, node: Node, column: float, row: float, columns: float, rows: float,
                 key: str, scope: dict[str, str] | None = None):
        self.node = node
        self.column = column
        self.row = row
        self.columns = columns
        self.rows = rows
        self.key = key
        #: ``$name`` values this placement adds - one repeat item's, if any
        self.scope = scope or {}


class Count(Widget):
    """The "+N more" a clipped container owes the operator.

    A container told not to page still may not lie about what it is holding.
    """

    def __init__(self, hidden: int):
        super().__init__()
        self.hidden = hidden

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        blit_text(surface, ctx.book, f"+{self.hidden} MORE", ctx.font_px(ctx.theme.size_micro),
                  ctx.theme.inop, (self.rect.right, self.rect.bottom), anchor="bottomright", mono=True)


class DashboardScreen(Screen):
    """The custom shell: one node tree, laid out into the content rectangle."""

    key = "dashboard"
    title = "DASHBOARD"

    def __init__(self, app: Any, dashboard: Dashboard):
        super().__init__(app)
        self.dashboard = dashboard
        self.plan = getattr(app, "plan", None)
        #: tabs path -> index of the pane showing
        self.selected: dict[str, int] = {}
        #: container path -> page showing
        self.pages: dict[str, int] = {}
        #: ``$name`` values a goto carried
        self.params: dict[str, str] = {}
        self._history: list[tuple[dict[str, int], dict[str, str]]] = []
        self._widgets: list[Widget] = []
        self._parents, self._paths = _index(dashboard.root)
        if dashboard.start:
            self.goto(dashboard.start, {}, remember=False)

    # -- navigation ------------------------------------------------------
    def invalidate(self) -> None:
        """Drop the layout, and make sure the frame that replaces it is drawn.

        Navigation here is not always driven by an input event - a goto can
        come from a signal handler - and the loop only paints when it thinks
        something moved.
        """
        super().invalidate()
        request = getattr(self.app, "request_redraw", None)
        if request is not None:
            request()

    @property
    def subtitle(self) -> str:
        pane = self._current_pane()
        if pane is None:
            return ""
        return str(pane.props.get("title") or pane.id or "").upper()

    def _current_pane(self) -> Node | None:
        node = self.dashboard.root
        while node.type == "tabs" and node.children:
            index = self.selected.get(self._paths[id(node)], 0)
            node = node.children[min(index, len(node.children) - 1)]
        return node if node is not self.dashboard.root else None

    def goto(self, node_id: str, params: dict[str, str], *, remember: bool = True) -> None:
        """Show the node with this id, selecting whatever tabs contain it."""
        target = self.dashboard.node(node_id)
        if target is None:
            return
        if remember:
            self._history.append((dict(self.selected), dict(self.params)))
        child = target
        parent = self._parents.get(id(child))
        while parent is not None:
            if parent.type == "tabs" and child in parent.children:
                self.selected[self._paths[id(parent)]] = parent.children.index(child)
            child, parent = parent, self._parents.get(id(parent))
        self.params.update(params)
        self.invalidate()

    def back(self) -> None:
        if not self._history:
            return
        self.selected, self.params = self._history.pop()
        self.invalidate()

    def cycle(self, step: int = 1) -> None:
        """Next pane of the tabs at root - what TAB and the number keys drive."""
        root = self.dashboard.root
        if root.type != "tabs" or not root.children:
            return
        path = self._paths[id(root)]
        self.select(path, (self.selected.get(path, 0) + step) % len(root.children))

    def cycle_to(self, index: int) -> None:
        """Show pane ``index`` of the tabs at root - the number shortcuts."""
        root = self.dashboard.root
        if root.type != "tabs" or not (0 <= index < len(root.children)):
            return
        self.select(self._paths[id(root)], index)

    def select(self, path: str, index: int) -> None:
        self.selected[path] = index
        self.invalidate()

    def run_action(self, action: Action, extra: dict[str, str], scope: dict[str, str]) -> None:
        """Do what a tap asked for, with ``$name`` resolved from scope + item."""
        values = {**scope, **extra}
        if action.kind == "toggle":
            target = _expand(action.target, values)
            if target:
                self.app.backend.toggle(target)
        elif action.kind == "goto":
            params = {k: str(_expand(str(v), values)) for k, v in action.params.items()}
            self.goto(str(action.target), {**extra, **params})
        elif action.kind == "back":
            self.back()
        elif action.kind == "call":
            domain, _, service = str(action.service).partition(".")
            data = {k: _expand(v, values) if isinstance(v, str) else v
                    for k, v in action.data.items()}
            entity = _expand(action.target, values) or data.pop("entity_id", None)
            self.app.backend.call(domain, service, entity, **data)

    # -- layout ----------------------------------------------------------
    def layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        self._widgets = []
        gap = ctx.u(ctx.theme.gap)
        self.unit_w = (rect.width - gap * (ROOT_COLUMNS - 1)) / ROOT_COLUMNS
        self.unit_h = (rect.height - gap * (ROOT_ROWS - 1)) / ROOT_ROWS
        self._layout_node(self.dashboard.root, rect, ctx, dict(self.params), "0")

    def _strip(self, rows: float, gap: float) -> float:
        """Height of a strip that eats ``rows`` of its container's grid."""
        return rows * self.unit_h + gap * (rows - 1)

    def _capacity(self, rect: pygame.Rect, gap: float) -> tuple[float, float]:
        """How many units of grid a rectangle actually holds."""
        columns = (rect.width + gap) / max(self.unit_w + gap, 1e-6)
        rows = (rect.height + gap) / max(self.unit_h + gap, 1e-6)
        return columns, rows

    def _layout_node(self, node: Node, rect: pygame.Rect, ctx: UIContext,
                     scope: dict[str, str], path: str) -> None:
        if node.type == "tabs":
            self._layout_tabs(node, rect, ctx, scope, path)
        elif node.type in CONTAINERS:
            self._layout_container(node, rect, ctx, scope, path)
        else:
            self._layout_component(node, rect, ctx, scope)

    def _layout_component(self, node: Node, rect: pygame.Rect, ctx: UIContext,
                          scope: dict[str, str]) -> None:
        make = builder(node.type)
        if make is None:
            raise DashboardError(f"no builder for {node.type!r}", line=node.line)
        bc = BuildContext(
            ctx=ctx, plan=self.plan, scope=scope,
            run_action=lambda action, extra, s=scope: self.run_action(action, extra, s),
        )
        widget = make(node, bc)
        widget.layout(rect)
        self._widgets.append(widget)

    def _layout_tabs(self, node: Node, rect: pygame.Rect, ctx: UIContext,
                     scope: dict[str, str], path: str) -> None:
        panes = self._children(node, scope, path)
        if not panes:
            return
        gap = ctx.u(ctx.theme.gap)
        placement = str(node.props.get("bar", "bottom"))
        index = min(self.selected.get(path, 0), len(panes) - 1)
        if placement == "none":
            # a stack, not a tab strip: the panes are reached by goto alone,
            # which is how a drill-down keeps its stages out of the nav bar
            pane = panes[index]
            self._layout_node(pane.node, rect, ctx, {**scope, **pane.scope}, pane.key)
            return
        bar_h = min(max(self._strip(TABS_ROWS, gap), MIN_TABS_PX), rect.height * 0.4)
        box = Box(rect)
        if placement == "top":
            bar, body = box.rows(bar_h, rect.height - bar_h - gap, gap=gap)
        else:
            body, bar = box.rows(rect.height - bar_h - gap, bar_h, gap=gap)

        for position, (cell, placed) in enumerate(zip(bar.cols(*[1.0] * len(panes), gap=gap), panes)):
            label = str(placed.node.props.get("title") or placed.node.id or position + 1)
            tab = Button(label, (lambda p=path, i=position: self.select(p, i)), compact=True,
                         sub=str(position + 1))
            tab.active = position == index
            tab.layout(cell.rect)
            self._widgets.append(tab)

        pane = panes[index]
        self._layout_node(pane.node, body.rect, ctx, {**scope, **pane.scope}, pane.key)

    def _layout_container(self, node: Node, rect: pygame.Rect, ctx: UIContext,
                          scope: dict[str, str], path: str) -> None:
        gap = ctx.u(ctx.theme.gap)
        columns, rows = self._capacity(rect, gap)
        children = self._children(node, scope, path)
        if not children:
            return
        shelves = _flow(node, children, max(1.0, columns), max(0.5, rows))
        total = sum(shelf_height for _, shelf_height in _shelf_rows(shelves))

        box = Box(rect)
        page = self.pages.get(path, 0)
        pages = [shelves]
        if total > rows + EPS:
            if node.overflow == "auto" and rows >= PAGER_MIN_ROWS - EPS:
                pager_h = max(self._strip(PAGER_ROWS, gap), MIN_PAGER_PX)
                box, pager = box.rows(rect.height - pager_h - gap, pager_h, gap=gap)
                # the pager took a row: re-flow, so a child that asked for
                # more rows than are left is clamped to what is left rather
                # than drawn straight through the strip
                _, rows = self._capacity(box.rect, gap)
                shelves = _flow(node, children, max(1.0, columns), max(0.5, rows))
                pages = _paginate(shelves, rows)
                page = max(0, min(page, len(pages) - 1))
                self.pages[path] = page
                self._layout_pager(pager, page, len(pages), path, ctx, gap)
            else:
                pages = [_clip(shelves, rows)]
                page = 0

        shown = pages[page]
        hidden = sum(len(shelf) for shelf in shelves) - sum(len(shelf) for shelf in shown)
        offset = shown[0][0].row if shown and shown[0] else 0.0
        for shelf in shown:
            for placed in shelf:
                child_rect = self._rect_of(box.rect, placed, offset, gap)
                self._layout_node(placed.node, child_rect,
                                  ctx, {**scope, **placed.scope}, placed.key)
        if hidden > 0 and node.overflow != "auto":
            more = Count(hidden)
            more.layout(box.rect)
            self._widgets.append(more)

    def _layout_pager(self, pager: Box, page: int, pages: int, path: str,
                      ctx: UIContext, gap: float) -> None:
        prev_cell, next_cell = pager.cols(1, 1, gap=gap)
        prev = Button("<", lambda p=path: self._turn(p, -1), compact=True, sub=f"{page + 1}/{pages}")
        nxt = Button(">", lambda p=path: self._turn(p, 1), compact=True, sub=f"{page + 1}/{pages}")
        prev.enabled = page > 0
        nxt.enabled = page < pages - 1
        prev.layout(prev_cell.rect)
        nxt.layout(next_cell.rect)
        self._widgets += [prev, nxt]

    def _turn(self, path: str, step: int) -> None:
        self.pages[path] = max(0, self.pages.get(path, 0) + step)
        self.invalidate()

    def _rect_of(self, rect: pygame.Rect, placed: Placed, row_offset: float,
                 gap: float) -> pygame.Rect:
        left = rect.left + placed.column * (self.unit_w + gap)
        top = rect.top + (placed.row - row_offset) * (self.unit_h + gap)
        width = placed.columns * self.unit_w + gap * (placed.columns - 1)
        height = placed.rows * self.unit_h + gap * (placed.rows - 1)
        return pygame.Rect(round(left), round(top), max(1, round(width)), max(1, round(height)))

    def _children(self, node: Node, scope: dict[str, str], path: str) -> list[Placed]:
        """The nodes a container holds: its own, then whatever it repeats."""
        chips = node.type == "chips"
        out = [Placed(_compact(child) if chips else child, 0, 0,
                      child.span.columns, child.span.rows, f"{path}/{index}")
               for index, child in enumerate(node.children)]
        if node.repeat is None:
            return out
        template = node.repeat.template
        for index, values in enumerate(self._repeat_items(node, scope)):
            out.append(Placed(template, 0, 0, template.span.columns, template.span.rows,
                              f"{path}/r{index}", values))
        return out

    def _repeat_items(self, node: Node, scope: dict[str, str]) -> list[dict[str, str]]:
        repeat = node.repeat
        if repeat is None or self.plan is None:
            return []
        selector = replace(
            repeat.selector,
            room=_expand(repeat.selector.room, scope),
            zone=_expand(repeat.selector.zone, scope),
            floor=_expand(repeat.selector.floor, scope),
            kind=_expand(repeat.selector.kind, scope),
        )
        if repeat.over == "places":
            return [{"entity": place.entity_ids[0] if place.entity_ids else "",
                     "room": place.room, "zone": place.zone, "name": place.name}
                    for place in select_places(self.plan, selector)]
        return [{"entity": device.entity_id, "room": device.room or "",
                 "name": device.display_label}
                for device in select_devices(self.plan, selector)]

    # -- events and drawing ----------------------------------------------
    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        for widget in reversed(self._widgets):
            if widget.visible and widget.enabled and widget.handle(event, ctx):
                return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_BACKSPACE:
            self.back()
            return True
        return False

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        for widget in self._widgets:
            if widget.visible:
                widget.draw(surface, ctx)


# -- flow ----------------------------------------------------------------
def _flow(container: Node, children: list[Placed], columns: float,
          rows: float) -> list[list[Placed]]:
    """Pack children row-major into shelves, in units."""
    shelves: list[list[Placed]] = []
    shelf: list[Placed] = []
    cursor = 0.0
    row = 0.0
    height = 0.0
    one_per_shelf = container.type == "rows"
    for placed in children:
        span_c, span_r = child_span(container.type, placed.node, len(children), columns, rows)
        if shelf and (one_per_shelf or cursor + span_c > columns + EPS):
            shelves.append(shelf)
            shelf = []
            row += height
            cursor = height = 0.0
        placed.column, placed.row = cursor, row
        placed.columns, placed.rows = span_c, span_r
        shelf.append(placed)
        cursor += span_c
        height = max(height, span_r)
    if shelf:
        shelves.append(shelf)
    return shelves


def _shelf_rows(shelves: list[list[Placed]]) -> list[tuple[list[Placed], float]]:
    return [(shelf, max((p.rows for p in shelf), default=0.0)) for shelf in shelves]


def _paginate(shelves: list[list[Placed]], rows: float) -> list[list[list[Placed]]]:
    """Group shelves into pages, each no taller than ``rows`` units."""
    pages: list[list[list[Placed]]] = []
    page: list[list[Placed]] = []
    used = 0.0
    for shelf, height in _shelf_rows(shelves):
        if page and used + height > rows + EPS:
            pages.append(page)
            page, used = [], 0.0
        page.append(shelf)
        used += height
    if page:
        pages.append(page)
    return pages or [[]]


def _clip(shelves: list[list[Placed]], rows: float) -> list[list[Placed]]:
    kept: list[list[Placed]] = []
    used = 0.0
    for shelf, height in _shelf_rows(shelves):
        if kept and used + height > rows + EPS:
            break
        kept.append(shelf)
        used += height
    return kept


def _compact(node: Node) -> Node:
    """A chip is a compact control; the container says so, not every child."""
    if node.props.get("compact"):
        return node
    return replace(node, props={**node.props, "compact": True})


def _expand(text: str | None, scope: dict[str, str]) -> str | None:
    if not isinstance(text, str) or "$" not in text:
        return text
    for name, value in scope.items():
        text = text.replace(f"${name}", str(value))
    return text


def _index(root: Node) -> tuple[dict[int, Node], dict[int, str]]:
    """Parent and path tables for the static tree, for navigation."""
    parents: dict[int, Node] = {}
    paths: dict[int, str] = {id(root): "0"}

    def walk(node: Node, path: str) -> None:
        for index, child in enumerate(node.children):
            parents[id(child)] = node
            paths[id(child)] = f"{path}/{index}"
            walk(child, f"{path}/{index}")

    walk(root, "0")
    return parents, paths
