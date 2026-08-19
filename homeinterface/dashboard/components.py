"""The component catalogue: one builder per ``type:`` an author may write.

A builder turns one :class:`~.schema.Node` into one widget.  Where a stock
widget already does the job it is used as-is; the three composites here
(``places``, ``device-rows``, ``floorplan``) are the "already complex" pieces
a dashboard is expected to reach for rather than assemble.

Live values are not baked in at build time: a builder that shows state returns
a :class:`Dynamic`, whose refresh hook runs each frame.  That keeps one layout
pass valid until the rectangle changes, which is what the panel can afford.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pygame

from ..floorplan import FloorRenderer, PlanView, device_at
from ..floorplan.model import BBox, Device, Floor, FloorPlan, Room
from ..fonts import blit_text, truncate
from ..scaling import Box
from ..theme import mix
from ..screens.alt import (
    CARD_H,
    CARD_MIN_W,
    PAGER_H,
    ROW_H,
    ROW_MIN_W,
    DeviceRow,
    PlaceCard,
    PowerChip,
    _columns,
    _fits,
    _page_slice,
)
from ..ui.base import UIContext, Widget, WidgetGroup
from ..ui.controls import Button, Slider
from ..ui.indicators import ArcGauge, BarGauge, Clock, EntityTile, MessageStrip, Panel, Readout, StatusLamp
from .registry import register
from .schema import Action, Binding, Node, Predicate, Selector

#: ``$name`` in any string property is replaced from the navigation scope
_PARAM = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")
#: ``{...}`` is a lookup - a property of this node's entity, or of one named
#: outright.  Nothing else: no filters, no arithmetic, no calls (docs/adr/0002)
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


# -- evaluation ----------------------------------------------------------
def evaluate(predicate: Predicate, backend, entity_id: str | None) -> bool:
    """Answer one predicate against the backend."""
    target = predicate.entity or entity_id
    entity = backend.get(target) if target else None
    if predicate.kind == "exists":
        return (entity is not None) == bool(predicate.value)
    if entity is None:
        return False
    raw = entity.state if predicate.attribute == "state" else entity.attributes.get(predicate.attribute)
    if predicate.kind == "state":
        return str(raw).strip().lower() == str(predicate.value).strip().lower()
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return False
    return number > float(predicate.value) if predicate.kind == "above" else number < float(predicate.value)


def _lookup(path: str, binding: Binding | None, backend) -> str:
    """Resolve one ``{...}`` placeholder to text.

    ``attributes.x`` and bare property names read this node's own entity; three
    or more segments name another entity outright.
    """
    if path.startswith("attributes."):
        entity_id, prop = (binding.entity if binding else None), path
    elif path.count(".") >= 2:
        domain, obj, prop = path.split(".", 2)
        entity_id, prop = f"{domain}.{obj}", prop
    else:
        entity_id, prop = (binding.entity if binding else None), path
    if not entity_id:
        return ""
    entity = backend.get(entity_id)
    if entity is None:
        return "--"
    if prop.startswith("attributes."):
        value = entity.attributes.get(prop.split(".", 1)[1])
    elif prop == "state":
        value = entity.state
    elif prop == "name":
        value = entity.name
    elif prop == "id":
        value = entity.entity_id
    elif prop == "level":
        value = entity.level
    else:
        value = entity.attributes.get(prop)
    if value is None:
        return "--"
    own = binding is not None and entity_id == binding.entity
    return _format(value, binding if own else None)


def _format(value: Any, binding: Binding | None) -> str:
    """Print a value the way its binding asks: precision, then unit."""
    text = str(value)
    if binding is not None and binding.precision is not None:
        try:
            text = f"{float(value):.{int(binding.precision)}f}"
        except (TypeError, ValueError):
            pass
    if binding is not None and binding.unit:
        text = f"{text} {binding.unit}"
    return text


# -- build context -------------------------------------------------------
@dataclass
class BuildContext:
    """What a builder is given besides its node."""

    ctx: UIContext
    plan: FloorPlan
    #: ``$name`` values in scope for this node (navigation params, repeat vars)
    scope: dict[str, str] = field(default_factory=dict)
    #: run one action, optionally with extra ``$name`` values from the item hit
    run_action: Callable[[Action, dict[str, str]], None] = lambda action, extra: None
    #: a mutable dict scoped to this node's path, for the rare widget whose
    #: state (zoom, pan, ...) must survive a rebuild - see build.py's
    #: ``DashboardScreen.floorplan_view``.  Not a general facility: only
    #: ``floorplan`` reaches for this today.
    state: Callable[[], dict] = lambda: {}

    def param(self, text: Any) -> Any:
        """Substitute ``$name`` from scope; non-strings pass through."""
        if not isinstance(text, str):
            return text
        return _PARAM.sub(lambda m: str(self.scope.get(m.group(1), m.group(0))), text)

    def prop(self, node: Node, key: str, default: Any = None) -> Any:
        value = node.props.get(key, default)
        return self.param(value)

    def binding(self, node: Node) -> Binding | None:
        if node.binding is None:
            return None
        return Binding(str(self.param(node.binding.entity)), node.binding.precision, node.binding.unit)

    def text(self, node: Node, key: str, default: str = "") -> Callable[[UIContext], str]:
        """A closure that renders one text property against live state."""
        template = str(self.prop(node, key, default) or "")
        binding = self.binding(node)

        def render(ctx: UIContext) -> str:
            return _PLACEHOLDER.sub(lambda m: _lookup(m.group(1), binding, ctx.backend), template)

        return render

    def level(self, node: Node, default: str = "normal") -> Callable[[UIContext], str]:
        binding = self.binding(node)
        rules = node.levels

        def resolve(ctx: UIContext) -> str:
            for rule in rules:
                if rule.predicate is None or evaluate(rule.predicate, ctx.backend,
                                                      binding.entity if binding else None):
                    return rule.level
            return default

        return resolve

    def press(self, node: Node, extra: Callable[[], dict[str, str]] | None = None) -> Callable[[], None]:
        action = node.action
        if action is None:
            return lambda: None
        return lambda: self.run_action(action, extra() if extra else {})

    def selector(self, node: Node) -> Selector:
        return Selector(room=self.prop(node, "room"), zone=self.prop(node, "zone"),
                        floor=self.prop(node, "floor"), kind=self.prop(node, "kind"))


class Dynamic(Widget):
    """A widget plus the per-frame refresh that keeps it honest.

    Visibility is re-checked on every event as well as every frame: a node
    hidden by ``visible_if`` must not answer a tap that lands where it used to
    be.
    """

    def __init__(self, inner: Widget, refresh: Callable[[UIContext], None] | None = None,
                 *, visible_if: Predicate | None = None, entity: str | None = None):
        super().__init__()
        self.inner = inner
        self.refresh = refresh
        self.visible_if = visible_if
        self.entity = entity

    def layout(self, rect: pygame.Rect) -> None:
        super().layout(rect)
        self.inner.layout(rect)

    def _shown(self, ctx: UIContext) -> bool:
        if self.visible_if is None:
            return True
        return evaluate(self.visible_if, ctx.backend, self.entity)

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        if not (self.visible and self.enabled and self._shown(ctx)):
            return False
        return self.inner.handle(event, ctx)

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        if not self._shown(ctx):
            return
        if self.refresh is not None:
            self.refresh(ctx)
        self.inner.draw(surface, ctx)


def wrap(node: Node, bc: BuildContext, inner: Widget,
         refresh: Callable[[UIContext], None] | None = None) -> Widget:
    binding = bc.binding(node)
    return Dynamic(inner, refresh, visible_if=node.visible_if,
                   entity=binding.entity if binding else None)


# -- simple components ---------------------------------------------------
@register("button")
def _button(node: Node, bc: BuildContext) -> Widget:
    label = bc.text(node, "label", node.props.get("text", ""))
    sub = bc.text(node, "sub")
    level = bc.level(node, "info")
    widget = Button("", bc.press(node), compact=bool(node.props.get("compact", False)))

    def refresh(ctx: UIContext) -> None:
        widget.label = label(ctx)
        widget.sub = sub(ctx) or None
        widget.level = level(ctx)

    return wrap(node, bc, widget, refresh)


@register("toggle")
def _toggle(node: Node, bc: BuildContext) -> Widget:
    binding = bc.binding(node)
    if binding is not None:
        entity_id = binding.entity
        group_ids = None
    else:
        ids = _entity_ids(node, bc)
        entity_id = ids[0] if ids else ""
        group_ids = ids
    label = bc.text(node, "label", "{name}")
    widget = ToggleAction(entity_id, bc, node, group_ids)

    def refresh(ctx: UIContext) -> None:
        widget.label_override = label(ctx)

    return wrap(node, bc, widget, refresh)


class ToggleAction(Button):
    """A toggle that reflects its entity and, by default, commands it.

    An ``on_press`` on the node wins: the same widget then reads one entity and
    does something else entirely, which is how a row can show a light and open
    a detail pane instead of switching it.

    ``group_ids`` is set when the node has no ``binding`` but a
    ``entities:``/selector shape instead: the widget then reads and commands
    the whole group via the backend's group operations rather than one entity.
    """

    def __init__(self, entity_id: str, bc: BuildContext, node: Node,
                 group_ids: list[str] | None = None):
        super().__init__(entity_id, None, level="on")
        self.entity_id = entity_id
        self.group_ids = group_ids
        self.label_override = ""
        self._press = bc.press(node) if node.action is not None else None
        self._level = bc.level(node, "")

    def activate(self, ctx: UIContext) -> None:
        if self._press is not None:
            self._press()
            return
        if self.group_ids is not None:
            ctx.backend.toggle_group(self.group_ids)
            return
        ctx.backend.toggle(self.entity_id)

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        if self.group_ids is not None:
            on, total = ctx.backend.group_state(self.group_ids)
            self.active = on > 0
            self.enabled = total > 0
            self.level = self._level(ctx) or ("on" if self.active else "off")
            self.label = self.label_override or self.entity_id
            self.sub = f"{on}/{total}" if total else "INOP"
        else:
            entity = ctx.backend.get(self.entity_id)
            self.active = bool(entity and entity.is_on)
            self.enabled = bool(entity and entity.available)
            self.level = self._level(ctx) or (entity.level if entity else "inop")
            self.label = self.label_override or (entity.name if entity else self.entity_id)
            state = "ON" if self.active else "OFF"
            if entity is not None and not entity.available:
                state = "N/A"
            elif entity is None:
                state = "INOP"
            self.sub = state
        Button.draw(self, surface, ctx)


@register("slider")
def _slider(node: Node, bc: BuildContext) -> Widget:
    binding = bc.binding(node)
    if binding is not None:
        ids = [binding.entity]
        grouped = False
    else:
        ids = _entity_ids(node, bc)
        grouped = True
    entity_id = ids[0] if ids else ""
    kind = str(bc.prop(node, "control", "brightness"))
    label = str(bc.prop(node, "label", kind.upper()))
    backend = bc.ctx.backend
    entity = backend.get(entity_id) if entity_id else None

    if kind == "temperature":
        value = (entity.number("temperature", 22.0) if entity else 22.0) or 22.0
        on_commit = ((lambda v, i=ids: backend.set_group_temperature(i, v)) if grouped
                    else (lambda v, eid=entity_id: backend.set_temperature(eid, v)))
        widget = Slider(minimum=float(bc.prop(node, "min", 16)), maximum=float(bc.prop(node, "max", 30)),
                        step=0.5, value=value, label=label, unit=str(bc.prop(node, "unit", "°C")),
                        on_commit=on_commit)
        if entity is not None:
            widget.target = entity.number("current_temperature")
    elif kind == "position":
        # covers have no group setter (Backend.set_group_*) - a grouped
        # position slider still commands only the first entity in the group
        value = (entity.number("current_position", 100.0) if entity else 0.0) or 0.0
        widget = Slider(value=value, label=label, unit=str(bc.prop(node, "unit", "%")),
                        on_commit=lambda v, eid=entity_id: backend.set_cover_position(eid, v))
    else:
        # mirrors screens/plan.py's _build_zone_inspector: a grouped slider's
        # value comes from the first entity, gated by whether any is on
        on_now = True
        if grouped and ids:
            on, _total = backend.group_state(ids)
            on_now = on > 0
        level = float(entity.attributes.get("brightness", 0)) / 2.55 if entity else 0.0
        value = level if on_now else 0.0
        on_commit = ((lambda v, i=ids: backend.set_group_brightness(i, v)) if grouped
                    else (lambda v, eid=entity_id: backend.set_brightness(eid, v)))
        widget = Slider(value=value, label=label, unit=str(bc.prop(node, "unit", "%")),
                        on_commit=on_commit)
    return wrap(node, bc, widget)


@register("tile")
def _tile(node: Node, bc: BuildContext) -> Widget:
    binding = bc.binding(node)
    entity_id = binding.entity if binding else ""
    press = bc.press(node) if node.action is not None else None
    widget = EntityTile(entity_id, on_press=(lambda _e: press()) if press else None)
    return wrap(node, bc, widget)


@register("readout")
def _readout(node: Node, bc: BuildContext) -> Widget:
    label = bc.text(node, "label", "{name}")
    value = bc.text(node, "value", "{state}")
    level = bc.level(node, "normal")
    widget = Readout("", "--", str(bc.prop(node, "unit", "") or ""))

    def refresh(ctx: UIContext) -> None:
        widget.label = label(ctx)
        widget.value = value(ctx)
        widget.level = level(ctx)

    return wrap(node, bc, widget, refresh)


@register("arc-gauge")
def _arc_gauge(node: Node, bc: BuildContext) -> Widget:
    return _gauge(node, bc, ArcGauge)


@register("bar-gauge")
def _bar_gauge(node: Node, bc: BuildContext) -> Widget:
    return _gauge(node, bc, BarGauge)


def _gauge(node: Node, bc: BuildContext, cls) -> Widget:
    binding = bc.binding(node)
    attribute = str(bc.prop(node, "attribute", "state"))
    label = bc.text(node, "label", "{name}")
    widget = cls(str(bc.prop(node, "label", "")).upper(),
                 minimum=float(bc.prop(node, "min", 0)), maximum=float(bc.prop(node, "max", 100)),
                 unit=str(bc.prop(node, "unit", "") or ""))

    def refresh(ctx: UIContext) -> None:
        widget.label = label(ctx).upper()
        entity = ctx.backend.get(binding.entity) if binding else None
        widget.value = (entity.number(attribute, 0.0) if entity else 0.0) or 0.0

    return wrap(node, bc, widget, refresh)


@register("lamp")
def _lamp(node: Node, bc: BuildContext) -> Widget:
    caption = bc.text(node, "label", "{name}")
    level = bc.level(node, "")
    binding = bc.binding(node)
    widget = StatusLamp("", "inop")

    def refresh(ctx: UIContext) -> None:
        entity = ctx.backend.get(binding.entity) if binding else None
        widget.caption = caption(ctx).upper()
        widget.level = level(ctx) or (entity.level if entity else "inop")

    return wrap(node, bc, widget, refresh)


@register("messages")
def _messages(node: Node, bc: BuildContext) -> Widget:
    widget = MessageStrip(max_lines=int(bc.prop(node, "lines", 4)))

    def refresh(ctx: UIContext) -> None:
        widget.lines = [(a.level, a.text) for a in ctx.backend.alerts()]

    return wrap(node, bc, widget, refresh)


@register("clock")
def _clock(node: Node, bc: BuildContext) -> Widget:
    widget = Clock(utc=bool(node.props.get("utc", False)))
    widget.compact = bool(node.props.get("compact", False))
    return wrap(node, bc, widget)


@register("panel")
def _panel(node: Node, bc: BuildContext) -> Widget:
    title = bc.text(node, "title", str(node.props.get("label", "")))
    widget = Panel("")

    def refresh(ctx: UIContext) -> None:
        widget.title = title(ctx).upper()

    return wrap(node, bc, widget, refresh)


@register("spacer")
def _spacer(node: Node, bc: BuildContext) -> Widget:
    class _Blank(Widget):
        def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
            return

    return wrap(node, bc, _Blank())


@register("label")
def _label(node: Node, bc: BuildContext) -> Widget:
    text = bc.text(node, "text", "{state}")

    class _Label(Widget):
        def __init__(self) -> None:
            super().__init__()
            self.text = ""

        def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
            t = ctx.theme
            size = ctx.font_px(t.size_body)
            blit_text(surface, ctx.book, self.text, size, t.text,
                      (self.rect.left, self.rect.centery), anchor="midleft")

    widget = _Label()

    def refresh(ctx: UIContext) -> None:
        widget.text = text(ctx)

    return wrap(node, bc, widget, refresh)


#: the fixed attribute whitelist attr-list prints, one line each when present
_ATTR_KEYS = ("brightness", "current_temperature", "temperature", "current_position")


@register("attr-list")
def _attr_list(node: Node, bc: BuildContext) -> Widget:
    """Bound to one entity: STATE plus whichever attributes in _ATTR_KEYS exist.

    Mirrors screens/alt.py's _draw_attributes and screens/plan.py's inspector
    attribute block - same key list, same "KEY  value" formatting, same
    colours. The line count is dynamic per entity, which is why this is a
    small dedicated widget rather than a placeholder sequence (ADR 0005).
    """

    binding = bc.binding(node)
    entity_id = binding.entity if binding else ""

    class _AttrList(Widget):
        def __init__(self) -> None:
            super().__init__()
            #: (text, colour) computed on the last draw - exposed for tests
            self.lines: list[tuple[str, tuple[int, int, int]]] = []

        def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
            t = ctx.theme
            entity = ctx.backend.get(entity_id) if entity_id else None
            size = ctx.font_px(t.size_small)
            lines: list[tuple[str, tuple[int, int, int]]] = []
            if entity is None:
                lines.append(("ENTITY NOT IN BACKEND", t.caution))
            else:
                lines.append((f"STATE  {entity.state.upper()}", t.text))
                for key in _ATTR_KEYS:
                    if key in entity.attributes:
                        lines.append((f"{key.upper():<20}{entity.attributes[key]}", t.data))
            self.lines = lines
            for i, (text, colour) in enumerate(lines):
                blit_text(surface, ctx.book,
                          truncate(ctx.book, text, size, self.rect.width - ctx.u(16), mono=True),
                          size, colour,
                          (self.rect.left + ctx.u(8), self.rect.top + ctx.u(6) + i * size * 1.5),
                          anchor="topleft", mono=True)

    return wrap(node, bc, _AttrList())


@register("link-status")
def _link_status(node: Node, bc: BuildContext) -> Widget:
    """Backend-level connection diagnostics: no binding, nothing entity-shaped.

    Mirrors screens/systems.py's _draw_diagnostics - same rows, formatting
    and colours.
    """

    class _LinkStatus(Widget):
        def __init__(self) -> None:
            super().__init__()
            #: (label, value, colour) computed on the last draw - for tests
            self.rows: list[tuple[str, str, tuple[int, int, int]]] = []

        def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
            t = ctx.theme
            backend = ctx.backend
            size = ctx.font_px(t.size_small)
            micro = ctx.font_px(t.size_micro)
            rows: list[tuple[str, str, tuple[int, int, int]]] = [
                ("STATE", backend.link.value.upper(), t.status_color(backend.link.level)),
                ("BACKEND", type(backend).__name__.replace("Backend", "").upper(), t.text),
                ("ENTITIES", str(len(backend.snapshot())), t.data),
                ("ALERTS", str(len(backend.alerts())), t.caution if backend.alerts() else t.normal),
                ("REVISION", str(backend.revision), t.inop),
            ]
            self.rows = rows
            y = self.rect.top
            for label, value, colour in rows:
                blit_text(surface, ctx.book, label, micro, t.inop,
                          (self.rect.left, y), anchor="topleft")
                blit_text(surface, ctx.book, value, size, colour,
                          (self.rect.right, y - ctx.u(2)), anchor="topright", mono=True)
                y += size * 1.6

            error = backend.last_error
            if error:
                blit_text(surface, ctx.book, truncate(ctx.book, error.upper(), micro, self.rect.width),
                          micro, mix(t.warning, t.text, 0.2), (self.rect.left, self.rect.bottom),
                          anchor="bottomleft", mono=True)

    return wrap(node, bc, _LinkStatus())


@register("power-chip")
def _power_chip(node: Node, bc: BuildContext) -> Widget:
    ids = _entity_ids(node, bc)
    widget = PowerChip(ids, lambda: bc.ctx.backend.toggle_group(ids))
    if node.action is not None:
        widget.on_press = bc.press(node)
    return wrap(node, bc, widget)


def _entity_ids(node: Node, bc: BuildContext) -> list[str]:
    """The entities a group control commands: a binding, a list, or a selector."""
    binding = bc.binding(node)
    if binding is not None:
        return [binding.entity]
    listed = node.props.get("entities")
    if isinstance(listed, list):
        return [str(bc.param(e)) for e in listed]
    return [d.entity_id for d in select_devices(bc.plan, bc.selector(node))]


# -- selection over the plan ---------------------------------------------
def select_devices(plan: FloorPlan, selector: Selector) -> list[Device]:
    """Every device matching ``selector``; an empty field matches everything.

    Room membership is the plan's, not the file's: a device with no ``room:``
    belongs to whichever room's polygon it stands in, which is how the plan
    was written and how the hand-built shells read it.
    """
    floors = [f for f in plan.floors if selector.floor in (None, "", f.id)]
    if selector.zone:
        zone = plan.zone(selector.zone)
        devices = plan.zone_devices(zone) if zone is not None else []
        if selector.room:
            inside = {d.entity_id for f in floors for d in f.devices_in(selector.room)}
            devices = [d for d in devices if d.entity_id in inside]
    elif selector.room:
        devices = [d for floor in floors for d in floor.devices_in(selector.room)]
    else:
        devices = [d for floor in floors for d in floor.devices]
    if selector.kind:
        devices = [d for d in devices if d.resolved_kind == selector.kind]
    return devices


def select_floors(plan: FloorPlan, selector: Selector) -> list[Floor]:
    """Every floor matching ``selector.floor``; only that field applies."""
    return [f for f in plan.floors if selector.floor in (None, "", f.id)]


def select_rooms(plan: FloorPlan, selector: Selector) -> list[tuple[Floor, Room]]:
    """Every room matching ``room``/``zone``/``floor``, paired with its floor."""
    out: list[tuple[Floor, Room]] = []
    for floor in select_floors(plan, selector):
        for room in floor.rooms:
            if selector.room and room.id != selector.room:
                continue
            if selector.zone:
                zone = plan.zone_of(floor.id, room.id)
                if zone is None or zone.id != selector.zone:
                    continue
            out.append((floor, room))
    return out


@dataclass(frozen=True)
class Place:
    """One unit of control: a zone, or a room that belongs to no zone.

    Exactly one of ``room`` and ``zone`` is set, so a place can be handed
    straight to a selector as ``room: $room, zone: $zone`` - the empty one
    matches everything and the set one does the work.
    """

    name: str
    room: str
    zone: str
    entity_ids: list[str]
    area: float = 0.0

    @property
    def is_zone(self) -> bool:
        return bool(self.zone)


def select_places(plan: FloorPlan, selector: Selector) -> list[Place]:
    """One entry per unit of control, in plan order."""
    floors = [f for f in plan.floors if selector.floor in (None, "", f.id)]
    out: list[Place] = []
    seen: set[str] = set()
    for floor in floors:
        for room in floor.rooms:
            zone = plan.zone_of(floor.id, room.id)
            if zone is None:
                if selector.zone:
                    continue
                out.append(Place(room.name, room.id, "",
                                 [d.entity_id for d in floor.devices_in(room.id)], room.area))
            elif zone.id not in seen and selector.zone in (None, "", zone.id):
                seen.add(zone.id)
                out.append(Place(zone.name, "", zone.id,
                                 [d.entity_id for d in plan.zone_devices(zone)],
                                 plan.zone_area(zone)))
    return out


# -- composites ----------------------------------------------------------
class _Paged(WidgetGroup):
    """Shared paging for the two list composites.

    These own their pages rather than borrowing the container's: a list is one
    node, and a node that overflows its own rectangle cannot ask its parent for
    another row.
    """

    def __init__(self, ctx: UIContext):
        super().__init__()
        self.ctx = ctx
        self.page = 0
        self.pages = 1
        self.prev = Button("<", lambda: self._turn(-1), compact=True)
        self.next = Button(">", lambda: self._turn(1), compact=True)

    def _turn(self, step: int) -> None:
        self.page = max(0, min(self.pages - 1, self.page + step))
        self.layout(self.rect)

    def _paginate(self, box: Box, count: int, per_row: int, min_h: float, gap: float) -> Box:
        """Reserve a pager strip when the list does not fit, and page it."""
        rows_needed = -(-count // max(per_row, 1))
        if rows_needed > _fits(box.rect.height, min_h, gap):
            pager_h = max(self.ctx.u(PAGER_H), 34)
            box, pager = box.rows(box.rect.height - pager_h - gap, pager_h, gap=gap)
            prev, nxt = pager.cols(1, 1, gap=gap)
            self.prev.layout(prev.rect)
            self.next.layout(nxt.rect)
            per_page = _fits(box.rect.height, min_h, gap) * max(per_row, 1)
            self.pages = max(1, -(-count // max(per_page, 1)))
            self.page = min(self.page, self.pages - 1)
            self.prev.enabled = self.page > 0
            self.next.enabled = self.page < self.pages - 1
            self.prev.sub = self.next.sub = f"{self.page + 1}/{self.pages}"
            self.add(self.prev)
            self.add(self.next)
        else:
            self.pages = 1
            self.page = 0
        return box

    def _grid(self, box: Box, count: int, cols: int, min_h: float, max_h: float,
              gap: float) -> list[pygame.Rect]:
        """Row-major rectangles for ``count`` items, sized by the grid not the count."""
        rows = max(1, _fits(box.rect.height, min_h, gap))
        height = min(max_h, (box.rect.height - gap * (rows - 1)) / rows)
        width = (box.rect.width - gap * (cols - 1)) / cols
        return [pygame.Rect(round(box.rect.left + (i % cols) * (width + gap)),
                            round(box.rect.top + (i // cols) * (height + gap)),
                            round(width), round(height))
                for i in range(count)]


class PlacesGrid(_Paged):
    """Every zone and lone room as a card, with a power target on each."""

    def __init__(self, ctx: UIContext, plan: FloorPlan, selector: Selector,
                 on_open: Callable[["Place"], None]):
        super().__init__(ctx)
        self.places = select_places(plan, selector)
        self.units = plan.units
        self.on_open = on_open

    def layout(self, rect: pygame.Rect) -> None:
        Widget.layout(self, rect)
        self.clear()
        if not self.places:
            return
        gap = self.ctx.u(self.ctx.theme.gap)
        box = Box(rect)
        cols = _columns(rect.width, self.ctx.u(CARD_MIN_W), gap)
        min_h = max(self.ctx.u(CARD_H * 0.8), 40)
        box = self._paginate(box, len(self.places), cols, min_h, gap)
        per_page = _fits(box.rect.height, min_h, gap) * cols
        page = _page_slice(self.places, per_page, self.page)
        cells = self._grid(box, len(page), cols, min_h, max(self.ctx.u(CARD_H * 1.4), 60), gap)
        chip_w = max(self.ctx.u(self.ctx.theme.touch_min), 40)
        for place, cell in zip(page, cells):
            ids = place.entity_ids
            sub = f"{place.area:.0f} {self.units}²"
            card = PlaceCard(place.name, sub, ids, (lambda p=place: self.on_open(p)),
                             is_zone=place.is_zone)
            card.chip_w = chip_w + self.ctx.u(8)
            card.layout(cell)
            self.add(card)
            if ids:
                chip = PowerChip(ids, (lambda i=ids: self.ctx.backend.toggle_group(i)))
                chip.layout(pygame.Rect(round(cell.right - chip_w - self.ctx.u(6)),
                                        round(cell.centery - chip_w / 2),
                                        round(chip_w), round(chip_w)))
                self.add(chip)


class DeviceList(_Paged):
    """The devices a selector picks out, one row each."""

    def __init__(self, ctx: UIContext, plan: FloorPlan, selector: Selector,
                 on_open: Callable[[str], None], columns_limit: int = 3):
        super().__init__(ctx)
        self.devices = select_devices(plan, selector)
        self.on_open = on_open
        self.columns_limit = columns_limit

    def layout(self, rect: pygame.Rect) -> None:
        Widget.layout(self, rect)
        self.clear()
        if not self.devices:
            return
        gap = self.ctx.u(self.ctx.theme.gap)
        box = Box(rect)
        cols = _columns(rect.width, self.ctx.u(ROW_MIN_W), gap, limit=self.columns_limit)
        min_h = max(self.ctx.u(ROW_H * 0.7), 30)
        box = self._paginate(box, len(self.devices), cols, min_h, gap)
        per_page = _fits(box.rect.height, min_h, gap) * cols
        page = _page_slice(self.devices, per_page, self.page)
        cells = self._grid(box, len(page), cols, min_h, max(self.ctx.u(ROW_H * 1.15), 46), gap)
        for device, cell in zip(page, cells):
            row = DeviceRow(device.entity_id, device.display_label,
                            (lambda e=device.entity_id: self.on_open(e)))
            row.layout(cell)
            self.add(row)


#: mirrors screens/plan.py's module constants of the same name
ZOOM_STEP = 1.18
MIN_ZOOM = 0.5
MAX_ZOOM = 6.0


class FloorPlanView(WidgetGroup):
    """The scale drawing: rooms, walls, devices, live states.

    Two stages, mirroring ``screens/plan.py``'s ``PlanScreen``:

    * **overview** (``focus`` empty) - the whole floor, names only, small
      markers.  A tap reports the room (or its zone) it landed in via
      ``on_room``, so a dashboard can drill down the way it drills down from
      a place card.
    * **focus** (``focus`` set to a room or zone id) - just that room, or
      that zone's rooms, filling the rect with bigger markers and area
      labels.  A tap on a device is hit-tested and hovered, and reports the
      tapped entity id via ``on_select`` - a distinct callback from
      ``on_room``, because the two fire at different stages for different
      purposes (docs/adr/0006): ``on_room``/``on_press`` navigates to a new
      pane, ``on_select``/``set`` writes one param into the current one.
      Likewise, tapping the room around a focused device does *not* exit
      focus the way stock does - the dashboard has no notion of "the pane
      before this one" for a component to invent on its own; an author
      routes back out with an explicit ``button``/``back``.

    Zoom (wheel + in/out/reset buttons, bottom-right of its own rect) and pan
    (right-drag) are intrinsic - always available, in both stages.  Their
    state lives in ``view_state``, a dict owned by the long-lived
    :class:`~.build.DashboardScreen` and merely referenced here: a fresh
    ``FloorPlanView`` is built on every relayout, so state kept as a plain
    instance attribute would reset on the next unrelated invalidate (see
    ``BuildContext.state`` / ``DashboardScreen.floorplan_view``).
    """

    def __init__(self, ctx: UIContext, plan: FloorPlan, floor_id: str | None,
                 on_room: Callable[[str], None] | None, *,
                 focus: str = "", view_state: dict | None = None,
                 on_select: Callable[[str], None] | None = None):
        super().__init__()
        self.ctx = ctx
        self.plan = plan
        self.floor_id = floor_id or (plan.floors[0].id if plan.floors else "")
        self.on_room = on_room
        self.on_select = on_select
        self.focus = focus
        self.renderer = FloorRenderer(ctx.theme, ctx.book)
        self.hover_device: str | None = None
        self._panning = False
        self._pan_anchor = (0, 0)
        # a dict handed to us by BuildContext.state(): mutating it in place
        # (never replacing it) is what makes zoom/pan survive a rebuild
        self._view_state = view_state if view_state is not None else {}
        self._view_state.setdefault("zoom", 1.0)
        self._view_state.setdefault("pan", (0.0, 0.0))

        self.btn_zoom_in = Button("+", lambda: self._zoom(ZOOM_STEP), compact=True)
        self.btn_zoom_out = Button("-", lambda: self._zoom(1 / ZOOM_STEP), compact=True)
        self.btn_reset = Button("FIT", self._reset_view, compact=True)
        self.children = [self.btn_zoom_out, self.btn_zoom_in, self.btn_reset]

    # -- persisted view state ---------------------------------------------
    @property
    def zoom(self) -> float:
        return self._view_state["zoom"]

    @zoom.setter
    def zoom(self, value: float) -> None:
        self._view_state["zoom"] = value

    @property
    def pan(self) -> tuple[float, float]:
        return self._view_state["pan"]

    @pan.setter
    def pan(self, value: tuple[float, float]) -> None:
        self._view_state["pan"] = value

    def _zoom(self, factor: float) -> None:
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))

    def _reset_view(self) -> None:
        self.zoom = 1.0
        self.pan = (0.0, 0.0)

    # -- state ---------------------------------------------------------
    @property
    def floor(self):
        return self.plan.floor(self.floor_id)

    @property
    def focused(self) -> bool:
        return bool(self.focus)

    def _focus_zone(self):
        """The zone ``focus`` names or belongs to, if any.

        ``focus`` may be bound to ``$room`` or to ``$zone`` (docs/adr/0006),
        so its value is tried as a zone id first, then as a room id whose
        zone (if any) is what stock's ``_enter_focus`` would have picked.
        """
        if not self.focus:
            return None
        zone = self.plan.zone(self.focus)
        if zone is not None:
            return zone
        floor = self.floor
        room = next((r for r in floor.rooms if r.id == self.focus), None) if floor else None
        return self.plan.zone_of(self.floor_id, room.id) if room else None

    @property
    def focus_rooms(self) -> frozenset[str]:
        """The rooms drawn in focus mode; empty in overview."""
        if not self.focused:
            return frozenset()
        zone = self._focus_zone()
        if zone is not None:
            return frozenset(zone.room_ids_on(self.floor_id))
        floor = self.floor
        room = next((r for r in floor.rooms if r.id == self.focus), None) if floor else None
        return frozenset({room.id}) if room else frozenset()

    def _focus_devices(self) -> list[Device]:
        floor = self.floor
        if floor is None or not self.focused:
            return []
        out: list[Device] = []
        seen: set[str] = set()
        for room_id in self.focus_rooms:
            for device in floor.devices_in(room_id):
                if device.entity_id not in seen:
                    seen.add(device.entity_id)
                    out.append(device)
        return out

    def _zone_labels(self) -> dict[str, str | None]:
        """Overview naming: one label per zone, on its largest room."""
        floor = self.floor
        if floor is None:
            return {}
        by_id = {r.id: r for r in floor.rooms}
        labels: dict[str, str | None] = {}
        for zone in self.plan.zones_on(self.floor_id):
            members = [by_id[i] for i in zone.room_ids_on(self.floor_id) if i in by_id]
            if not members:
                continue
            host = max(members, key=lambda r: (r.label_extent[0] * r.label_extent[1], r.area))
            for room in members:
                labels[room.id] = zone.name if room is host else None
        return labels

    def _focus_bbox(self) -> BBox | None:
        """Extent of the focused room/zone, or None in overview."""
        floor = self.floor
        ids = self.focus_rooms
        if floor is None or not ids:
            return None
        boxes = [r.bbox for r in floor.rooms if r.id in ids]
        if not boxes:
            return None
        box = boxes[0]
        for other in boxes[1:]:
            box = box.merged(other)
        for device in self._focus_devices():
            box = box.merged(BBox.around([device.at]))
        return box.expanded(max(0.35, min(box.width, box.height) * 0.10))

    def _current_view(self) -> PlanView | None:
        """Rebuilt every call rather than cached: zoom/pan can change without
        a relayout, and the renderer's own static-layer cache already makes
        this cheap."""
        floor = self.floor
        if floor is None or self.rect.width <= 0 or self.rect.height <= 0:
            return None
        bbox = self._focus_bbox() if self.focused else None
        bbox = bbox or self.plan.common_bbox.expanded(0.4)
        return PlanView(bbox, self.rect, zoom=self.zoom, pan=self.pan)

    # -- layout ----------------------------------------------------------
    def layout(self, rect: pygame.Rect) -> None:
        Widget.layout(self, rect)
        t = self.ctx.theme
        gap = self.ctx.u(t.gap)
        btn = max(self.ctx.u(t.touch_min), 36)
        zoom_w = btn * 3 + gap * 2
        controls = Box(pygame.Rect(
            rect.right - self.ctx.u(t.pad) - zoom_w,
            rect.bottom - self.ctx.u(t.pad) - btn,
            zoom_w, btn,
        ))
        for widget, cell in zip(
            (self.btn_zoom_out, self.btn_zoom_in, self.btn_reset),
            controls.cols(1, 1, 1, gap=gap),
        ):
            widget.layout(cell.rect)

    # -- events ------------------------------------------------------------
    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        if WidgetGroup.handle(self, event, ctx):
            return True
        floor = self.floor
        view = self._current_view()
        if floor is None or view is None:
            return False

        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(ctx.pointer):
            self._zoom(ZOOM_STEP if event.y > 0 else 1 / ZOOM_STEP)
            return True

        if event.type == pygame.MOUSEMOTION:
            if self._panning:
                dx = (event.pos[0] - self._pan_anchor[0]) / view.scale
                dy = (event.pos[1] - self._pan_anchor[1]) / view.scale
                self.pan = (self.pan[0] - dx, self.pan[1] - dy)
                self._pan_anchor = event.pos
                return True
            if self.rect.collidepoint(event.pos) and self.focused:
                hit = device_at(floor, view, event.pos, ctx.u(18), self._focus_devices())
                self.hover_device = hit.entity_id if hit else None
            else:
                self.hover_device = None
            return False

        if (event.type == pygame.MOUSEBUTTONDOWN and event.button in (2, 3)
                and self.rect.collidepoint(event.pos)):
            self._panning = True
            self._pan_anchor = event.pos
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button in (2, 3):
            self._panning = False
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.rect.collidepoint(event.pos):
            if not self.focused:
                room = floor.room_at(view.to_plan(event.pos))
                if room is not None and self.on_room is not None:
                    self.on_room(room.id)
                return True
            # focused: a device tap reports the entity id via on_select.  A
            # tap on the room around the device does nothing new: stock's
            # "tap outside exits focus" is not replicated (see class docstring).
            hit = device_at(floor, view, event.pos, ctx.u(20), self._focus_devices())
            if hit is not None and self.on_select is not None:
                self.on_select(hit.entity_id)
            return True

        return False

    # -- drawing -----------------------------------------------------------
    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        view = self._current_view()
        floor = self.floor
        if view is None or floor is None:
            blit_text(surface, ctx.book, "NO PLAN", ctx.font_px(ctx.theme.size_small),
                      ctx.theme.caution, self.rect.center, anchor="center", mono=True)
            return
        t = ctx.theme
        devices = self._focus_devices() if self.focused else floor.devices
        states = {d.entity_id: (e.level if (e := ctx.backend.get(d.entity_id)) else "inop")
                  for d in devices}
        self.renderer.render(
            surface, floor, view, ctx.vp,
            states=states,
            hover_device=self.hover_device if self.focused else None,
            units=self.plan.units,
            devices=devices,
            visible_rooms=self.focus_rooms if self.focused else None,
            room_labels={} if self.focused else self._zone_labels(),
            show_area=self.focused,
            label_sizes=((t.size_large, t.size_body, t.size_small, t.size_micro)
                         if self.focused else None),
            marker_max_u=26.0 if self.focused else 13.0,
        )
        WidgetGroup.draw(self, surface, ctx)


@register("places")
def _places(node: Node, bc: BuildContext) -> Widget:
    def open_place(place: Place) -> None:
        if node.action is not None:
            bc.run_action(node.action, {"room": place.room, "zone": place.zone,
                                        "name": place.name})

    return wrap(node, bc, PlacesGrid(bc.ctx, bc.plan, bc.selector(node), open_place))


@register("device-rows")
def _device_rows(node: Node, bc: BuildContext) -> Widget:
    def open_device(entity_id: str) -> None:
        if node.action is not None:
            bc.run_action(node.action, {"entity": entity_id, "device": entity_id})

    limit = int(bc.prop(node, "max_columns", 3))
    return wrap(node, bc, DeviceList(bc.ctx, bc.plan, bc.selector(node), open_device, limit))


@register("floorplan")
def _floorplan(node: Node, bc: BuildContext) -> Widget:
    def open_room(room_id: str) -> None:
        if node.action is None:
            return
        # a room inside a zone is commanded as its zone, the way a card is
        zone = next((bc.plan.zone_of(f.id, room_id) for f in bc.plan.floors
                     if any(r.id == room_id for r in f.rooms)), None)
        bc.run_action(node.action, {"room": "" if zone else room_id,
                                    "zone": zone.id if zone else ""})

    def select_device(entity_id: str) -> None:
        if node.select_action is not None:
            bc.run_action(node.select_action, {"entity": entity_id})

    floor_id = bc.prop(node, "floor")
    focus = str(bc.prop(node, "focus", "") or "")
    return wrap(node, bc, FloorPlanView(bc.ctx, bc.plan, floor_id,
                                        open_room if node.action is not None else None,
                                        focus=focus, view_state=bc.state(),
                                        on_select=select_device if node.select_action is not None else None))


# -- device-inspector, zone-inspector (docs/adr/0006) --------------------
class DeviceInspector(WidgetGroup):
    """Toggle + one kind-specific slider for a single entity.

    Mirrors ``screens/plan.py``'s ``_build_inspector`` device branch, but
    this only ever receives an entity id (a ``$device`` param a ``floorplan``
    tap set via the ``set`` action) rather than a plan ``Device``, so "kind"
    is read off the id's domain prefix instead of ``Device.resolved_kind`` -
    the same trick ``_build_zone_inspector``/this module's own ``_slider``
    already use for group operations.
    """

    def __init__(self, ctx: UIContext, entity_id: str):
        super().__init__()
        self.ctx = ctx
        self.entity_id = entity_id
        backend = ctx.backend
        entity = backend.get(entity_id) if entity_id else None
        domain = entity_id.split(".", 1)[0] if entity_id else ""

        self.toggle = Button(
            entity.name if entity else entity_id,
            (lambda: backend.toggle(entity_id)) if entity_id else (lambda: None),
            level="on", sub=entity_id,
        )
        self.add(self.toggle)

        self.slider: Slider | None = None
        if domain == "light":
            brightness = float(entity.attributes.get("brightness", 0)) / 2.55 if entity else 0.0
            self.slider = Slider(label="BRIGHTNESS", unit="%", value=brightness,
                                 on_commit=lambda v: backend.set_brightness(entity_id, v))
        elif domain == "climate" and entity is not None:
            target = entity.number("temperature", 22.0) or 22.0
            self.slider = Slider(minimum=16, maximum=30, step=0.5, value=target,
                                 label="TARGET", unit="°C",
                                 on_commit=lambda v: backend.set_temperature(entity_id, v))
            self.slider.target = entity.number("current_temperature")
        elif domain == "cover" and entity is not None:
            position = entity.number("current_position", 100.0) or 0.0
            self.slider = Slider(label="POSITION", unit="%", value=position,
                                 on_commit=lambda v: backend.set_cover_position(entity_id, v))
        if self.slider is not None:
            self.add(self.slider)

    def layout(self, rect: pygame.Rect) -> None:
        Widget.layout(self, rect)
        t = self.ctx.theme
        gap = self.ctx.u(t.gap)
        pad = self.ctx.u(t.pad)
        box = Box(rect)
        if self.slider is not None:
            head, body = box.rows(0.30, 0.70, gap=gap)
            self.toggle.layout(head.pad(0, head.rect.height * 0.18).rect)
            rows = body.rows(1, 1, 1, gap=gap)
            self.slider.layout(rows[0].pad(pad * 0.5, rows[0].rect.height * 0.28).rect)
        else:
            self.toggle.layout(box.pad(0, box.rect.height * 0.18).rect)


@register("device-inspector")
def _device_inspector(node: Node, bc: BuildContext) -> Widget:
    binding = bc.binding(node)
    entity_id = binding.entity if binding else ""
    return wrap(node, bc, DeviceInspector(bc.ctx, entity_id))


class ZoneInspector(WidgetGroup):
    """Master toggle + group sliders for a zone's (or room's) devices.

    Mirrors ``screens/plan.py``'s ``_build_zone_inspector`` - minus the
    ZONE/ROOM scope-toggle segmented control and the device-tile list, an
    explicit scope cut for this pass (docs/adr/0006): device tiles are
    reasonably built today from ``device-rows`` with a zone/room selector,
    and the scope toggle is a UX nuance rather than missing capability.
    """

    def __init__(self, ctx: UIContext, entity_ids: list[str], name: str):
        super().__init__()
        self.ctx = ctx
        self.entity_ids = entity_ids
        backend = ctx.backend
        lights = [i for i in entity_ids if i.startswith("light.")]
        climates = [i for i in entity_ids if i.startswith("climate.")]

        self.master = Button(name, lambda: backend.toggle_group(entity_ids), level="on",
                             sub=f"{len(entity_ids)} DEVICES")
        self.add(self.master)

        self.brightness: Slider | None = None
        if lights:
            on_now, _total = backend.group_state(lights)
            first = backend.get(lights[0])
            level = float(first.attributes.get("brightness", 0)) / 2.55 if first else 0.0
            self.brightness = Slider(
                label="ZONE BRIGHTNESS", unit="%", value=level if on_now else 0.0,
                on_commit=lambda v: backend.set_group_brightness(lights, v),
            )
            self.add(self.brightness)

        self.temperature: Slider | None = None
        if climates:
            first = backend.get(climates[0])
            target = (first.number("temperature", 22.0) if first else 22.0) or 22.0
            self.temperature = Slider(
                minimum=16, maximum=30, step=0.5, value=target, label="ZONE TARGET", unit="°C",
                on_commit=lambda v: backend.set_group_temperature(climates, v),
            )
            if first is not None:
                self.temperature.target = first.number("current_temperature")
            self.add(self.temperature)

    def layout(self, rect: pygame.Rect) -> None:
        Widget.layout(self, rect)
        t = self.ctx.theme
        gap = self.ctx.u(t.gap)
        box = Box(rect)
        master_h = max(self.ctx.u(t.touch_min), 36)
        self.master.layout(box.top_slice(master_h).rect)
        box = box.inset(top=master_h + gap)

        slider_h = max(self.ctx.u(30), 24)
        if self.brightness is not None:
            self.brightness.layout(box.top_slice(slider_h).inset(top=slider_h * 0.4).rect)
            box = box.inset(top=slider_h + gap)
        if self.temperature is not None:
            self.temperature.layout(box.top_slice(slider_h).inset(top=slider_h * 0.4).rect)
            box = box.inset(top=slider_h + gap)

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        on, total = ctx.backend.group_state(self.entity_ids)
        self.master.active = on > 0
        self.master.sub = f"{on}/{total} ON" if total else "NO DEVICES"
        WidgetGroup.draw(self, surface, ctx)


@register("zone-inspector")
def _zone_inspector(node: Node, bc: BuildContext) -> Widget:
    """Bound via ``zone:`` and/or ``room:`` props, resolved the way
    ``FloorPlanView._focus_zone`` resolves ``focus:`` - tried as a zone id
    first, then as a room id promoted to its zone.  Unlike ``focus:`` this
    component has no ``floor:`` in scope, so the room path searches every
    floor for a matching room id; a room with no zone (or naming no room at
    all) falls back to a plain room selector so an unzoned room still works.
    """
    zone_id = str(bc.prop(node, "zone", "") or "")
    room_id = str(bc.prop(node, "room", "") or "")
    plan = bc.plan
    zone = plan.zone(zone_id) if zone_id else None
    if zone is None and room_id:
        for floor in plan.floors:
            if any(r.id == room_id for r in floor.rooms):
                zone = plan.zone_of(floor.id, room_id)
                break
    if zone is not None:
        entity_ids = [d.entity_id for d in plan.zone_devices(zone)]
        name = zone.name
    else:
        entity_ids = [d.entity_id for d in select_devices(plan, Selector(room=room_id or None))]
        name = room_id or zone_id
    return wrap(node, bc, ZoneInspector(bc.ctx, entity_ids, name))
