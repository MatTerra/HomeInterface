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

from ..floorplan import FloorRenderer, PlanView
from ..floorplan.model import Device, FloorPlan
from ..fonts import blit_text
from ..scaling import Box
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
    entity_id = binding.entity if binding else ""
    label = bc.text(node, "label", "{name}")
    widget = ToggleAction(entity_id, bc, node)

    def refresh(ctx: UIContext) -> None:
        widget.label_override = label(ctx)

    return wrap(node, bc, widget, refresh)


class ToggleAction(Button):
    """A toggle that reflects its entity and, by default, commands it.

    An ``on_press`` on the node wins: the same widget then reads one entity and
    does something else entirely, which is how a row can show a light and open
    a detail pane instead of switching it.
    """

    def __init__(self, entity_id: str, bc: BuildContext, node: Node):
        super().__init__(entity_id, None, level="on")
        self.entity_id = entity_id
        self.label_override = ""
        self._press = bc.press(node) if node.action is not None else None
        self._level = bc.level(node, "")

    def activate(self, ctx: UIContext) -> None:
        if self._press is not None:
            self._press()
            return
        ctx.backend.toggle(self.entity_id)

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
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
    entity_id = binding.entity if binding else ""
    kind = str(bc.prop(node, "control", "brightness"))
    label = str(bc.prop(node, "label", kind.upper()))
    backend = bc.ctx.backend
    entity = backend.get(entity_id) if entity_id else None

    if kind == "temperature":
        value = (entity.number("temperature", 22.0) if entity else 22.0) or 22.0
        widget = Slider(minimum=float(bc.prop(node, "min", 16)), maximum=float(bc.prop(node, "max", 30)),
                        step=0.5, value=value, label=label, unit=str(bc.prop(node, "unit", "°C")),
                        on_commit=lambda v: backend.set_temperature(entity_id, v))
        if entity is not None:
            widget.target = entity.number("current_temperature")
    elif kind == "position":
        value = (entity.number("current_position", 100.0) if entity else 0.0) or 0.0
        widget = Slider(value=value, label=label, unit=str(bc.prop(node, "unit", "%")),
                        on_commit=lambda v: backend.set_cover_position(entity_id, v))
    else:
        level = float(entity.attributes.get("brightness", 0)) / 2.55 if entity else 0.0
        widget = Slider(value=level, label=label, unit=str(bc.prop(node, "unit", "%")),
                        on_commit=lambda v: backend.set_brightness(entity_id, v))
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
                                 [d.entity_id for d in floor.devices_in(room.id)]))
            elif zone.id not in seen and selector.zone in (None, "", zone.id):
                seen.add(zone.id)
                out.append(Place(zone.name, "", zone.id,
                                 [d.entity_id for d in plan.zone_devices(zone)]))
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
            card = PlaceCard(place.name, "", ids, (lambda p=place: self.on_open(p)),
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


class FloorPlanView(Widget):
    """The scale drawing: rooms, walls, devices, live states.

    A tap reports the room it landed in, so a dashboard can drill down from the
    drawing the same way it drills down from a card.
    """

    def __init__(self, ctx: UIContext, plan: FloorPlan, floor_id: str | None,
                 on_room: Callable[[str], None] | None):
        super().__init__()
        self.plan = plan
        self.floor_id = floor_id or (plan.floors[0].id if plan.floors else "")
        self.on_room = on_room
        self.renderer = FloorRenderer(ctx.theme, ctx.book)
        self._view: PlanView | None = None

    @property
    def floor(self):
        return self.plan.floor(self.floor_id)

    def layout(self, rect: pygame.Rect) -> None:
        super().layout(rect)
        self._view = None

    def _ensure_view(self) -> PlanView | None:
        floor = self.floor
        if floor is None or self.rect.width <= 0 or self.rect.height <= 0:
            return None
        if self._view is None:
            self._view = PlanView(self.plan.common_bbox.expanded(0.4), self.rect)
        return self._view

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        if self.on_room is None or event.type != pygame.MOUSEBUTTONUP or event.button != 1:
            return False
        if not self.rect.collidepoint(event.pos):
            return False
        view = self._ensure_view()
        floor = self.floor
        if view is None or floor is None:
            return False
        room = floor.room_at(view.to_plan(event.pos))
        if room is None:
            return False
        self.on_room(room.id)
        return True

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        view = self._ensure_view()
        floor = self.floor
        if view is None or floor is None:
            blit_text(surface, ctx.book, "NO PLAN", ctx.font_px(ctx.theme.size_small),
                      ctx.theme.caution, self.rect.center, anchor="center", mono=True)
            return
        states = {d.entity_id: (e.level if (e := ctx.backend.get(d.entity_id)) else "inop")
                  for d in floor.devices}
        self.renderer.render(surface, floor, view, ctx.vp, states=states, units=self.plan.units)


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

    floor_id = bc.prop(node, "floor")
    return wrap(node, bc, FloorPlanView(bc.ctx, bc.plan, floor_id,
                                        open_room if node.action is not None else None))
