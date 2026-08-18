"""Floor plan screen: the primary page.

The screen works in two stages, because a whole storey drawn small enough to
fit a 480x320 panel leaves neither the names nor the device markers legible:

* **overview** - the storey as geometry and names only.  No devices, no
  inspector, so the drawing gets the full width.  Rooms that belong to a zone
  are named for their zone, which is the unit the operator actually commands.
* **focus** - one room, or one zone, drawn alone and scaled to fill the plan
  rectangle, with its devices and the inspector beside it.

The overview has two presentations, switched by the GRID/PLAN button: the
drawing, and a grid of one card per place.  True to scale, a bathroom or a
corridor is a sliver you have to aim at; the grid gives every place the same
comfortable target and the same room for its name, at the cost of the spatial
sense the drawing carries.  Both stages lead into the same focus view.

Layout adapts to the panel's aspect: on a 21:9 wall display the inspector
sits beside the plan; on anything squarer it drops underneath.  The floor
selector is a vertical elevation strip - lowest storey at the bottom, the way
a lift indicator reads.
"""

from __future__ import annotations

import pygame

from .. import draw as vd
from ..floorplan import FloorRenderer, PlanView, device_at
from ..floorplan.model import BBox, Device, Floor, Room
from ..fonts import blit_text, truncate
from ..scaling import Box
from ..theme import mix
from ..ui.base import Pressable, UIContext, Widget
from ..ui.controls import Button, Slider
from ..ui.indicators import EntityTile, Panel
from .base import Screen

ZOOM_STEP = 1.18
MIN_ZOOM = 0.5
MAX_ZOOM = 6.0

#: smallest card the grid view will lay out, in design units.  Width is what a
#: room name needs at body size; height is a touch target plus its sub-line.
CARD_MIN_W = 104.0
CARD_MIN_H = 52.0
#: past four columns the cards stop being bigger than the rooms they replace
CARD_MAX_COLS = 4
#: cards read as labels, so they want to be wider than tall
CARD_ASPECT = 2.0


class FloorStrip(Widget):
    """Stacked storey selector, drawn as an elevation."""

    def __init__(self, floors: list[Floor], on_select):
        super().__init__()
        self.floors = floors
        self.on_select = on_select
        self.selected: str = floors[-1].id if floors else ""
        self._cells: list[tuple[str, pygame.Rect]] = []

    def layout(self, rect: pygame.Rect) -> None:
        super().layout(rect)
        self._cells = []
        if not self.floors:
            return
        # top of the strip is the highest level
        ordered = sorted(self.floors, key=lambda f: -f.level)
        cells = Box(rect).rows(*[1.0] * len(ordered), gap=rect.height * 0.02)
        self._cells = [(floor.id, cell.rect) for floor, cell in zip(ordered, cells)]

    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            for floor_id, cell in self._cells:
                if cell.collidepoint(event.pos):
                    self.selected = floor_id
                    self.on_select(floor_id)
                    return True
        return False

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        by_id = {f.id: f for f in self.floors}
        for floor_id, cell in self._cells:
            floor = by_id[floor_id]
            active = floor_id == self.selected
            cut = ctx.u(t.chamfer * 0.8)
            fill = mix(t.panel, t.data, 0.30) if active else mix(t.panel, t.background, 0.35)
            vd.chamfer_rect(surface, cell, fill=fill, cut=cut, corners="tlbr")
            vd.chamfer_rect(surface, cell, outline=t.data if active else t.rule,
                            width=ctx.px(t.stroke), cut=cut, corners="tlbr")
            tag_px = ctx.font_px(t.size_body)
            show_level = cell.height > tag_px * 2.4
            blit_text(surface, ctx.book, floor.tag, tag_px,
                      t.text if active else t.inop,
                      (cell.centerx, cell.centery - ctx.u(6) if show_level else cell.centery),
                      anchor="center")
            if show_level:
                blit_text(surface, ctx.book, f"L{floor.level:+d}", ctx.font_px(t.size_micro),
                          t.data if active else t.inop, (cell.centerx, cell.centery + ctx.u(12)),
                          anchor="center", mono=True)


class PlaceCard(Pressable):
    """One place in the grid view: a zone, or a room controlled on its own.

    The card carries what the drawing carries in overview - the name and the
    fact that something is on in there - and nothing else: it is a way in, not
    a control.  Acting on the place is still the focus stage's job.
    """

    def __init__(self, name: str, sub: str, room_id: str, entity_ids: list[str],
                 on_press, *, is_zone: bool = False):
        super().__init__(lambda: on_press(room_id))
        self.name = name
        self.sub = sub
        self.room_id = room_id
        self.entity_ids = entity_ids
        self.is_zone = is_zone

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        on, total = ctx.backend.group_state(self.entity_ids) if self.entity_ids else (0, 0)
        accent = t.status_color("on" if on else ("off" if total else "inop"))
        hovered = self.rect.collidepoint(ctx.pointer)
        cut = ctx.u(t.chamfer * 0.8)
        fill = mix(t.panel, t.panel_alt, 1.0 if (hovered or self.is_pressed) else 0.35)
        if on:
            fill = mix(fill, accent, 0.18)
        vd.chamfer_rect(surface, self.rect, fill=fill, cut=cut)
        vd.chamfer_rect(surface, self.rect,
                        outline=accent if on else (t.rule_bright if hovered else t.rule),
                        width=ctx.px(t.stroke), cut=cut)
        pygame.draw.rect(surface, accent,
                         pygame.Rect(self.rect.left, self.rect.top, ctx.px(3), self.rect.height))

        pad = ctx.u(8)
        inner = self.rect.width - pad * 2 - ctx.u(4)
        # the name is the card; type is capped against the card's own height so
        # a dense page does not print the name over its sub-line
        name_px = max(8, min(ctx.font_px(t.size_body), round(self.rect.height * 0.34)))
        sub_px = max(8, min(ctx.font_px(t.size_micro), round(self.rect.height * 0.22)))
        two_lines = self.rect.height > name_px + sub_px * 2.2
        blit_text(surface, ctx.book,
                  truncate(ctx.book, self.name.upper(), name_px, inner), name_px, t.text,
                  (self.rect.left + pad + ctx.u(4),
                   self.rect.top + pad if two_lines else self.rect.centery),
                  anchor="topleft" if two_lines else "midleft")
        if not two_lines:
            return
        state = f"{on}/{total} ON" if total else "NO DEVICES"
        blit_text(surface, ctx.book, truncate(ctx.book, state, sub_px, inner), sub_px,
                  accent if total else t.inop,
                  (self.rect.left + pad + ctx.u(4), self.rect.bottom - pad),
                  anchor="bottomleft", mono=True)
        # the area only earns its space once the state line is not alone
        if self.rect.width > ctx.u(CARD_MIN_W) * 1.15:
            blit_text(surface, ctx.book, truncate(ctx.book, self.sub, sub_px, inner * 0.5), sub_px,
                      t.inop, (self.rect.right - pad, self.rect.bottom - pad),
                      anchor="bottomright", mono=True)
        if self.is_zone:
            blit_text(surface, ctx.book, "ZONE", sub_px, t.data,
                      (self.rect.right - pad, self.rect.top + pad), anchor="topright", mono=True)


class PlanScreen(Screen):
    key = "plan"
    title = "PLAN"
    subtitle = "HOUSE LAYOUT / DEVICE MAP"
    HINT = "TAP A ROOM OR ZONE"

    def __init__(self, app):
        super().__init__(app)
        self.renderer = FloorRenderer(app.theme, app.book)
        self.plan = app.plan
        self.floor_id = self.plan.floors[0].id if self.plan.floors else ""
        self.zoom = 1.0
        self.pan = (0.0, 0.0)
        self.selected_device: str | None = None
        self.selected_room: str | None = None
        self.selected_zone: str | None = None
        #: stage: False = whole storey, names only; True = one room/zone alone
        self.focused = False
        #: overview presentation: False = the drawing, True = a grid of cards
        self.grid_mode = False
        self._cards: list[PlaceCard] = []
        self._grid_page = 0
        self._grid_pages = 1
        #: when the tapped room belongs to a zone, control the zone by default -
        #: the grouping exists precisely because that is the intended unit
        self.prefer_zone = True
        self.hover_device: str | None = None
        self._zone_master: Button | None = None
        self._zone_entity_ids: list[str] = []
        self._panning = False
        self._pan_anchor = (0, 0)
        self._view: PlanView | None = None
        self._plan_rect = pygame.Rect(0, 0, 1, 1)
        self._inspector_rect = pygame.Rect(0, 0, 1, 1)
        self._inspector: list[Widget] = []
        self._inspector_key: tuple | None = None

        self.strip = FloorStrip(self.plan.floors, self._select_floor)
        self.btn_zoom_in = Button("+", lambda: self._zoom(ZOOM_STEP), compact=True)
        self.btn_zoom_out = Button("-", lambda: self._zoom(1 / ZOOM_STEP), compact=True)
        self.btn_reset = Button("FIT", self._reset_view, compact=True)
        self.btn_back = Button("< PLAN", self._exit_focus, compact=True)
        # labelled with what a tap gives you, not with where you are
        self.btn_view = Button("GRID", self._toggle_view, compact=True)
        self.btn_page_prev = Button("<", lambda: self._turn_page(-1), compact=True)
        self.btn_page_next = Button(">", lambda: self._turn_page(1), compact=True)

    # -- state -----------------------------------------------------------
    @property
    def floor(self) -> Floor | None:
        return self.plan.floor(self.floor_id)

    @property
    def showing_zone(self) -> bool:
        return bool(self.selected_zone) and self.prefer_zone

    @property
    def zone_room_ids(self) -> frozenset[str]:
        """Member rooms of the active zone that live on the current floor."""
        zone = self.plan.zone(self.selected_zone) if self.selected_zone else None
        return frozenset(zone.room_ids_on(self.floor_id)) if zone else frozenset()

    @property
    def focus_rooms(self) -> frozenset[str]:
        """The rooms drawn in focus mode; empty in overview."""
        if not self.focused:
            return frozenset()
        if self.showing_zone:
            return self.zone_room_ids
        return frozenset({self.selected_room}) if self.selected_room else frozenset()

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
        """Overview naming: one label per zone, on its largest room.

        A zone exists because its rooms are operated together, so the overview
        names the unit the tap will select rather than the parts it is made of.
        """
        floor = self.floor
        if floor is None:
            return {}
        by_id = {r.id: r for r in floor.rooms}
        labels: dict[str, str | None] = {}
        for zone in self.plan.zones_on(self.floor_id):
            members = [by_id[i] for i in zone.room_ids_on(self.floor_id) if i in by_id]
            if not members:
                continue
            # The member with the most usable label space carries the name -
            # by free extent, not by area, since the largest room of a zone can
            # still be a corridor too narrow to print "SERVIÇOS" in.
            host = max(members, key=lambda r: (r.label_extent[0] * r.label_extent[1], r.area))
            for room in members:
                labels[room.id] = zone.name if room is host else None
        return labels

    def _select_floor(self, floor_id: str) -> None:
        self.floor_id = floor_id
        self._grid_page = 0  # another storey, another set of places
        self._exit_focus()

    def _enter_focus(self, room_id: str) -> None:
        zone = self.plan.zone_of(self.floor_id, room_id)
        self.selected_room = room_id
        self.selected_zone = zone.id if zone else None
        self.prefer_zone = zone is not None
        self.selected_device = None
        self.focused = True
        self._reset_view()
        self._inspector_key = None
        self.invalidate()  # the inspector column only exists in focus mode

    def _exit_focus(self) -> None:
        self.selected_device = None
        self.selected_room = None
        self.selected_zone = None
        self.focused = False
        self.hover_device = None
        # the column those widgets sat in is plan again: drop them now, or a
        # tap on the drawing lands on a button that is no longer there
        self._inspector = []
        self._reset_view()
        self._inspector_key = None
        self.invalidate()

    def _toggle_view(self) -> None:
        self.grid_mode = not self.grid_mode
        self._grid_page = 0
        self.invalidate()  # the plan controls exist in one presentation only

    def _turn_page(self, step: int) -> None:
        self._grid_page = max(0, min(self._grid_pages - 1, self._grid_page + step))
        self.invalidate()

    @property
    def showing_grid(self) -> bool:
        return self.grid_mode and not self.focused

    def _zoom(self, factor: float) -> None:
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))

    def _reset_view(self) -> None:
        self.zoom = 1.0
        self.pan = (0.0, 0.0)

    # -- layout ----------------------------------------------------------
    def layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        t = ctx.theme
        gap = ctx.u(t.gap)
        box = Box(rect)
        if not self.focused:
            # Nothing is selected, so the inspector would only say so: give the
            # whole rectangle to the drawing instead.
            plan_box, inspector_box = box, None
        elif ctx.vp.landscape:
            # 320px of height cannot spare a stacked inspector; keep it beside
            plan_box, inspector_box = box.cols(0.70, 0.30, gap=gap)
        else:
            plan_box, inspector_box = box.rows(0.66, 0.34, gap=gap)

        strip_w = max(ctx.u(34), 30)
        strip_box, plan_area = plan_box.cols(strip_w, plan_box.rect.width - strip_w - gap, gap=gap)
        self.strip.layout(strip_box.pad(0, strip_box.rect.height * 0.06).rect)
        self.strip.selected = self.floor_id

        self._plan_rect = plan_area.rect
        self._inspector_rect = inspector_box.rect if inspector_box else pygame.Rect(0, 0, 0, 0)

        # A vertical stack would claim a seventh of the plan's width on the
        # 480x320 panel, so the controls sit as a row along the bottom edge,
        # opposite the scale bar.
        btn = max(ctx.u(t.touch_min), 36)
        zoom_w = btn * 3 + gap * 2
        controls = Box(pygame.Rect(
            self._plan_rect.right - ctx.u(t.pad) - zoom_w,
            self._plan_rect.bottom - ctx.u(t.pad) - btn,
            zoom_w, btn,
        ))
        for widget, cell in zip(
            (self.btn_zoom_out, self.btn_zoom_in, self.btn_reset),
            controls.cols(1, 1, 1, gap=gap),
        ):
            widget.layout(cell.rect)
        # Back goes to the top right: the bottom left belongs to the scale bar
        # and the bottom right to the zoom cluster, and a mis-tap there would
        # throw away the selection instead of nudging the view.
        back_w = round(btn * 2.0)
        corner = pygame.Rect(
            self._plan_rect.right - ctx.u(t.pad) - back_w,
            self._plan_rect.top + ctx.u(t.pad),
            back_w, round(btn * 0.8),
        )
        self.btn_back.layout(corner)
        # BACK and GRID never coexist - one belongs to focus, the other to
        # overview - so they share the corner rather than crowd it.
        self.btn_view.layout(corner)
        self.btn_view.label = "PLAN" if self.grid_mode else "GRID"
        self.btn_view.active = self.grid_mode

        # zooming and fitting are things you do to a drawing; the grid keeps
        # only the page turners, in the same corner the zoom cluster sits in
        for widget in (self.btn_zoom_in, self.btn_zoom_out, self.btn_reset):
            widget.visible = not self.showing_grid
        if self.showing_grid:
            self._layout_grid(ctx, corner.bottom + gap, controls.rect)
        else:
            self._cards = []
            self.btn_page_prev.visible = self.btn_page_next.visible = False
        self._inspector_key = None

    # -- grid view -------------------------------------------------------
    def _grid_places(self) -> list[tuple[str, str, str, list[str], bool]]:
        """(name, sub, room_id, entity_ids, is_zone) per place on this floor.

        One card per *unit of control*: a zone counts once, however many rooms
        it is made of, exactly as the overview drawing names it once.  Order
        follows the plan file so the grid and the drawing list the house in the
        same sequence.
        """
        floor = self.floor
        if floor is None:
            return []
        units = self.plan.units
        out: list[tuple[str, str, str, list[str], bool]] = []
        seen_zones: set[str] = set()
        for room in floor.rooms:
            zone = self.plan.zone_of(self.floor_id, room.id)
            if zone is None:
                devices = floor.devices_in(room.id)
                out.append((room.name, f"{room.area:.0f} {units}²", room.id,
                            [d.entity_id for d in devices], False))
            elif zone.id not in seen_zones:
                seen_zones.add(zone.id)
                out.append((zone.name, f"{self.plan.zone_area(zone):.0f} {units}²", room.id,
                            [d.entity_id for d in self.plan.zone_devices(zone)], True))
        return out

    def _grid_shape(self, ctx: UIContext, rect: pygame.Rect, gap: float,
                    count: int) -> tuple[int, int]:
        """Columns and rows of cards that fit in ``rect``, capped by ``count``.

        Cards never go below the minimum touch size, so a page holds what it
        holds and the rest goes onto the next one.  Among the shapes that do
        hold everything, the one whose cells come closest to CARD_ASPECT wins:
        seven places on a wall panel want 4x2, not 2x4 of billboards.
        """
        cols_max = max(1, min(CARD_MAX_COLS,
                              int((rect.width + gap) // (ctx.u(CARD_MIN_W) + gap))))
        rows_max = max(1, int((rect.height + gap) // (ctx.u(CARD_MIN_H) + gap)))
        best: tuple[float, int, int] | None = None
        for cols in range(1, cols_max + 1):
            rows = max(1, -(-count // cols))
            if rows > rows_max:
                continue
            cell_w = (rect.width - gap * (cols - 1)) / cols
            cell_h = (rect.height - gap * (rows - 1)) / rows
            score = abs(cell_w / cell_h - CARD_ASPECT) if cell_h else 1e9
            if best is None or score < best[0]:
                best = (score, cols, rows)
        if best is None:  # nothing fits on one page: fill it and page the rest
            return cols_max, rows_max
        return best[1], best[2]

    def _layout_grid(self, ctx: UIContext, top: float, pager_rect: pygame.Rect) -> None:
        t = ctx.theme
        gap = ctx.u(t.gap) * 0.6
        pad = ctx.u(t.pad)
        area = Box(pygame.Rect(
            self._plan_rect.left + pad, round(top),
            self._plan_rect.width - pad * 2,
            round(self._plan_rect.bottom - pad - top),
        ))
        places = self._grid_places()
        self._cards = []
        if not places or area.rect.width <= 0 or area.rect.height <= 0:
            self._grid_pages = 1
            self.btn_page_prev.visible = self.btn_page_next.visible = False
            return

        cols, rows = self._grid_shape(ctx, area.rect, gap, len(places))
        paged = cols * rows < len(places)
        if paged:
            # the pager takes the strip the zoom cluster would have used, so
            # the cells have to be measured again against what is left
            area = area.inset(bottom=pager_rect.height + gap)
            cols, rows = self._grid_shape(ctx, area.rect, gap, len(places))
            half = (pager_rect.width - gap) / 2
            self.btn_page_prev.layout(pygame.Rect(round(pager_rect.left), pager_rect.top,
                                                  round(half), pager_rect.height))
            self.btn_page_next.layout(pygame.Rect(round(pager_rect.right - half), pager_rect.top,
                                                  round(half), pager_rect.height))
        per_page = cols * rows
        self._grid_pages = max(1, -(-len(places) // per_page))
        self._grid_page = min(self._grid_page, self._grid_pages - 1)
        self.btn_page_prev.enabled = self._grid_page > 0
        self.btn_page_next.enabled = self._grid_page < self._grid_pages - 1
        self.btn_page_prev.visible = self.btn_page_next.visible = paged

        page = places[self._grid_page * per_page:][:per_page]
        cells = area.grid(cols, rows, gap=gap)
        for (name, sub, room_id, entity_ids, is_zone), cell in zip(page, cells):
            card = PlaceCard(name, sub, room_id, entity_ids, self._enter_focus, is_zone=is_zone)
            card.layout(cell.rect)
            self._cards.append(card)

    def _make_view(self, ctx: UIContext) -> PlanView:
        return PlanView(
            self._focus_bbox() or self.plan.common_bbox.expanded(0.4),
            self._plan_rect,
            zoom=self.zoom,
            pan=self.pan,
        )

    def _focus_bbox(self) -> BBox | None:
        """Extent of the focused room/zone, or None in overview.

        Every floor shares one bbox in overview so storeys do not jump; in
        focus the opposite is wanted - the selection should fill the panel,
        which is the whole point of the second stage.
        """
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
        # devices sit at the wall line as often as not, and a marker clipped
        # in half is a marker you cannot tap
        for device in self._focus_devices():
            box = box.merged(BBox.around([device.at]))
        return box.expanded(max(0.35, min(box.width, box.height) * 0.10))

    # -- inspector -------------------------------------------------------
    def _build_inspector(self, ctx: UIContext) -> None:
        """Rebuild only when the selection or floor changes."""
        key = (self.floor_id, self.selected_device, self.selected_room, self.selected_zone,
               self.prefer_zone, self.focused, self._inspector_rect.size)
        if key == self._inspector_key:
            return
        self._inspector_key = key
        self._inspector = []
        floor = self.floor
        if floor is None:
            return

        t = ctx.theme
        gap = ctx.u(t.gap)
        pad = ctx.u(t.pad)
        area = Box(self._inspector_rect).pad(0)

        if self.selected_device:
            device = next((d for d in floor.devices if d.entity_id == self.selected_device), None)
            entity = ctx.backend.get(self.selected_device)
            if device is not None:
                head, body = area.rows(0.30, 0.70, gap=gap)
                toggle = Button(
                    entity.name if entity else device.display_label,
                    (lambda eid=device.entity_id: ctx.backend.toggle(eid)),
                    level="on",
                    sub=device.entity_id,
                )
                toggle.layout(head.pad(0, head.rect.height * 0.18).rect)
                self._inspector.append(toggle)

                rows = body.rows(1, 1, 1, gap=gap)
                if device.resolved_kind == "light":
                    brightness = float(entity.attributes.get("brightness", 0)) / 2.55 if entity else 0.0
                    slider = Slider(
                        label="BRIGHTNESS", unit="%", value=brightness,
                        on_commit=(lambda v, eid=device.entity_id: ctx.backend.set_brightness(eid, v)),
                    )
                    slider.layout(rows[0].pad(pad * 0.5, rows[0].rect.height * 0.28).rect)
                    self._inspector.append(slider)
                elif device.resolved_kind == "climate" and entity is not None:
                    target = entity.number("temperature", 22.0) or 22.0
                    slider = Slider(
                        minimum=16, maximum=30, step=0.5, value=target, label="TARGET", unit="°C",
                        on_commit=(lambda v, eid=device.entity_id: ctx.backend.set_temperature(eid, v)),
                    )
                    slider.target = entity.number("current_temperature")
                    slider.layout(rows[0].pad(pad * 0.5, rows[0].rect.height * 0.28).rect)
                    self._inspector.append(slider)
                elif device.resolved_kind == "cover" and entity is not None:
                    position = entity.number("current_position", 100.0) or 0.0
                    slider = Slider(
                        label="POSITION", unit="%", value=position,
                        on_commit=(lambda v, eid=device.entity_id: ctx.backend.set_cover_position(eid, v)),
                    )
                    slider.layout(rows[0].pad(pad * 0.5, rows[0].rect.height * 0.28).rect)
                    self._inspector.append(slider)
            return

        if self.showing_zone:
            self._build_zone_inspector(ctx, area, gap, pad)
            return

        if self.selected_room:
            if self.selected_zone:
                self._add_scope_toggle(ctx, area, gap)
                area = area.inset(top=self._scope_h(ctx) + gap)
            devices = floor.devices_in(self.selected_room)
            self._add_device_tiles(ctx, area.inset(top=ctx.u(26)), devices, gap)

    # -- zone control menu -----------------------------------------------
    def _scope_h(self, ctx: UIContext) -> float:
        return max(ctx.u(ctx.theme.touch_min * 0.75), 28)

    def _add_scope_toggle(self, ctx: UIContext, area: Box, gap: float) -> None:
        """[ ZONE | ROOM ] segmented control.

        A grouped room is still a room; the operator must be able to say
        "just this one" without editing the plan.
        """
        zone = self.plan.zone(self.selected_zone) if self.selected_zone else None
        if zone is None:
            return
        strip = area.top_slice(self._scope_h(ctx))
        zone_cell, room_cell = strip.cols(1, 1, gap=gap * 0.5)
        for label, cell, wants_zone in (
            (zone.tag, zone_cell, True),
            ("ROOM", room_cell, False),
        ):
            button = Button(label, (lambda z=wants_zone: self._set_scope(z)), compact=True)
            button.active = self.prefer_zone is wants_zone
            button.layout(cell.rect)
            self._inspector.append(button)

    def _add_device_tiles(self, ctx: UIContext, area: Box, devices: list, gap: float) -> None:
        if not devices:
            return
        tile_h = max(ctx.u(ctx.theme.touch_min * 0.8), 30)
        pitch = tile_h + gap * 0.6
        fits = max(1, int((area.rect.height + gap * 0.6) / pitch))
        for index, device in enumerate(devices[:fits]):
            tile = EntityTile(device.entity_id, on_press=self._select_device)
            tile.layout(pygame.Rect(area.rect.left, round(area.rect.top + index * pitch),
                                    area.rect.width, round(tile_h)))
            self._inspector.append(tile)

    def _build_zone_inspector(self, ctx: UIContext, area: Box, gap: float, pad: float) -> None:
        zone = self.plan.zone(self.selected_zone)
        if zone is None:
            return
        entity_ids = [d.entity_id for d in self.plan.zone_devices(zone)]
        lights = [i for i in entity_ids if i.startswith("light.")]
        climates = [i for i in entity_ids if i.startswith("climate.")]
        backend = ctx.backend

        self._add_scope_toggle(ctx, area, gap)
        body = area.inset(top=self._scope_h(ctx) + gap)

        master_h = max(ctx.u(ctx.theme.touch_min), 36)
        master = Button(
            zone.name,
            (lambda ids=entity_ids: backend.toggle_group(ids)),
            level="on",
            sub=f"{len(entity_ids)} DEVICES",
        )
        master.layout(body.top_slice(master_h).rect)
        self._inspector.append(master)
        self._zone_master = master
        self._zone_entity_ids = entity_ids
        body = body.inset(top=master_h + gap)

        slider_h = max(ctx.u(30), 24)
        if lights:
            on_now, _ = backend.group_state(lights)
            first = backend.get(lights[0])
            level = float(first.attributes.get("brightness", 0)) / 2.55 if first else 0.0
            slider = Slider(
                label="ZONE BRIGHTNESS", unit="%", value=level if on_now else 0.0,
                on_commit=(lambda v, ids=lights: backend.set_group_brightness(ids, v)),
            )
            slider.layout(body.top_slice(slider_h).inset(top=slider_h * 0.4).rect)
            self._inspector.append(slider)
            body = body.inset(top=slider_h + gap)

        if climates:
            first = backend.get(climates[0])
            target = (first.number("temperature", 22.0) if first else 22.0) or 22.0
            slider = Slider(
                minimum=16, maximum=30, step=0.5, value=target, label="ZONE TARGET", unit="°C",
                on_commit=(lambda v, ids=climates: backend.set_group_temperature(ids, v)),
            )
            if first is not None:
                slider.target = first.number("current_temperature")
            slider.layout(body.top_slice(slider_h).inset(top=slider_h * 0.4).rect)
            self._inspector.append(slider)
            body = body.inset(top=slider_h + gap)

        self._add_device_tiles(ctx, body, self.plan.zone_devices(zone), gap)

    def _set_scope(self, prefer_zone: bool) -> None:
        self.prefer_zone = prefer_zone
        self.selected_device = None
        self._reset_view()  # the drawn extent changes with the scope
        self._inspector_key = None

    def _select_device(self, entity_id: str) -> None:
        self.selected_device = entity_id
        self._inspector_key = None

    # -- events ----------------------------------------------------------
    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        controls: list[Widget] = [self.strip]
        if self.showing_grid:
            controls += [self.btn_page_prev, self.btn_page_next, *self._cards]
        else:
            controls += [self.btn_zoom_in, self.btn_zoom_out, self.btn_reset]
        controls.append(self.btn_back if self.focused else self.btn_view)
        for widget in controls:
            if widget.visible and widget.enabled and widget.handle(event, ctx):
                return True
        if self.focused:
            for widget in self._inspector:
                if widget.handle(event, ctx):
                    return True

        floor = self.floor
        view = self._view
        if self.showing_grid:
            # there is no drawing to pan, zoom or tap: the cards are the page
            return self._handle_keys(event)
        if floor is None or view is None:
            return False

        if event.type == pygame.MOUSEWHEEL and self._plan_rect.collidepoint(ctx.pointer):
            self._zoom(ZOOM_STEP if event.y > 0 else 1 / ZOOM_STEP)
            return True

        if event.type == pygame.MOUSEMOTION:
            if self._panning:
                dx = (event.pos[0] - self._pan_anchor[0]) / view.scale
                dy = (event.pos[1] - self._pan_anchor[1]) / view.scale
                self.pan = (self.pan[0] - dx, self.pan[1] - dy)
                self._pan_anchor = event.pos
                return True
            if self._plan_rect.collidepoint(event.pos) and self.focused:
                hit = device_at(floor, view, event.pos, ctx.u(18), self._focus_devices())
                self.hover_device = hit.entity_id if hit else None
            else:
                self.hover_device = None
            return False

        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (2, 3) and self._plan_rect.collidepoint(event.pos):
            self._panning = True
            self._pan_anchor = event.pos
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button in (2, 3):
            self._panning = False
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._plan_rect.collidepoint(event.pos):
            room = floor.room_at(view.to_plan(event.pos))
            if not self.focused:
                # stage one: a tap picks the place, nothing else is on the plan
                if room is not None:
                    self._enter_focus(room.id)
                return True

            hit = device_at(floor, view, event.pos, ctx.u(20), self._focus_devices())
            if hit is not None:
                if self.selected_device == hit.entity_id:
                    ctx.backend.toggle(hit.entity_id)  # second tap acts
                else:
                    self.selected_device = hit.entity_id
                self._inspector_key = None
                return True
            if room is None or room.id not in self.focus_rooms:
                # tapping the emptiness around the focused room is the natural
                # way back out of it
                self._exit_focus()
                return True
            # inside a zone, the tapped member becomes what "ROOM" scope means
            self.selected_room = room.id
            self.selected_device = None
            self._inspector_key = None
            return True

        return self._handle_keys(event)

    def _handle_keys(self, event: pygame.event.Event) -> bool:
        if event.type != pygame.KEYDOWN:
            return False
        # not ESC: the shell claims that one for quit
        if event.key == pygame.K_BACKSPACE and self.focused:
            self._exit_focus()
            return True
        if event.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
            step = 1 if event.key == pygame.K_PAGEUP else -1
            index = self.plan.index_of(self.floor_id) + step
            if 0 <= index < len(self.plan.floors):
                self._select_floor(self.plan.floors[index].id)
            return True
        if event.key == pygame.K_g and not self.focused:
            self._toggle_view()
            return True
        if event.key == pygame.K_f:
            self._reset_view()
            return True
        return False

    # -- drawing ---------------------------------------------------------
    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        floor = self.floor
        self._view = self._make_view(ctx)
        self._build_inspector(ctx)

        cut = ctx.u(t.chamfer)
        vd.chamfer_rect(surface, self._plan_rect, fill=t.background, cut=cut)
        vd.chamfer_rect(surface, self._plan_rect, outline=t.rule, width=ctx.px(t.stroke), cut=cut)

        if floor is not None and self.showing_grid:
            for card in self._cards:
                card.draw(surface, ctx)
            self._draw_caption(surface, ctx, floor)
        elif floor is None:
            blit_text(surface, ctx.book, "NO FLOOR PLAN LOADED", ctx.font_px(t.size_large), t.caution,
                      self._plan_rect.center, anchor="center")
            blit_text(surface, ctx.book, "SET plan: <file> IN config/app.yaml", ctx.font_px(t.size_small),
                      t.inop, (self._plan_rect.centerx, self._plan_rect.centery + ctx.u(28)),
                      anchor="midtop", mono=True)
        else:
            devices = self._focus_devices()
            states = {
                d.entity_id: (e.level if (e := ctx.backend.get(d.entity_id)) else "inop")
                for d in devices
            }
            labels = {} if self.focused else self._zone_labels()
            self.renderer.render(
                surface, floor, self._view, ctx.vp,
                states=states,
                selected_room=None if self.showing_zone else self.selected_room,
                selected_device=self.selected_device,
                hover_device=self.hover_device,
                zone_rooms=self.zone_room_ids if self.showing_zone else frozenset(),
                units=self.plan.units,
                devices=devices,
                visible_rooms=self.focus_rooms if self.focused else None,
                room_labels=labels,
                # stage one is a name map: areas are detail for stage two, and
                # a second line per room is what makes the overview unreadable
                show_area=self.focused,
                label_sizes=((t.size_large, t.size_body, t.size_small, t.size_micro)
                             if self.focused else None),
                # zoomed into one room the markers are the interface, not
                # annotation - let them grow to a real touch target
                marker_max_u=26.0 if self.focused else 13.0,
            )
            self._draw_caption(surface, ctx, floor)

        self.strip.draw(surface, ctx)
        for widget in (self.btn_zoom_in, self.btn_zoom_out, self.btn_reset,
                       self.btn_page_prev, self.btn_page_next):
            if widget.visible:
                widget.draw(surface, ctx)
        if self.focused:
            self.btn_back.draw(surface, ctx)
            self._draw_inspector(surface, ctx, floor)
        else:
            self.btn_view.draw(surface, ctx)


    def _hint_width(self, ctx: UIContext) -> float:
        return ctx.book.font(ctx.font_px(ctx.theme.size_micro), mono=True).size(self.HINT)[0]

    def _draw_caption(self, surface: pygame.Surface, ctx: UIContext, floor: Floor) -> None:
        """Top-left status line: what you are looking at, and how much of it."""
        t = ctx.theme
        if self.focused:
            devices = self._focus_devices()
            if self.showing_zone:
                zone = self.plan.zone(self.selected_zone)
                place = zone.name.upper() if zone else "ZONE"
                scope = f"{len(self.focus_rooms)} ROOMS"
            else:
                room = next((r for r in floor.rooms if r.id == self.selected_room), None)
                place = room.name.upper() if room else "ROOM"
                scope = f"{room.area:.1f} {self.plan.units}²" if room else ""
            parts = [floor.tag.upper(), place, scope, f"{len(devices)} DEVICES"]
        else:
            parts = [floor.name.upper(), f"{len(floor.rooms)} ROOMS",
                     f"{len(self.plan.zones_on(self.floor_id))} ZONES"]
            if self.showing_grid and self._grid_pages > 1:
                parts.append(f"PAGE {self._grid_page + 1}/{self._grid_pages}")
        left = self._plan_rect.left + ctx.u(t.pad)
        pad = ctx.u(t.pad)
        # the caption shares the top edge with a corner button - BACK in focus,
        # GRID/PLAN in overview - and with the tap hint, so it yields the space
        # rather than run under them
        button = self.btn_back if self.focused else self.btn_view
        hint_w = 0.0 if self.focused else self._hint_width(ctx) + pad
        limit = button.rect.left - pad
        # on the 480x320 panel the hint does not fit beside the caption; the
        # caption is the one that has to be there
        show_hint = not self.focused and limit - hint_w - left > ctx.u(60)
        size = ctx.font_px(t.size_micro)
        blit_text(surface, ctx.book,
                  truncate(ctx.book, "  ·  ".join(p for p in parts if p), size,
                           max(limit - (hint_w if show_hint else 0.0) - left, 0), mono=True),
                  size, t.inop, (left, self._plan_rect.top + ctx.u(t.pad)),
                  anchor="topleft", mono=True)
        if show_hint:
            blit_text(surface, ctx.book, self.HINT, size, t.inop,
                      (limit, self._plan_rect.top + ctx.u(t.pad)),
                      anchor="topright", mono=True)

    def _draw_inspector(self, surface: pygame.Surface, ctx: UIContext, floor: Floor | None) -> None:
        t = ctx.theme
        title = "SELECTION"
        if self.selected_device:
            title = "DEVICE"
        elif self.showing_zone:
            zone = self.plan.zone(self.selected_zone)
            title = f"ZONE · {zone.name}" if zone else "ZONE"
        elif self.selected_room and floor is not None:
            room = next((r for r in floor.rooms if r.id == self.selected_room), None)
            title = f"ROOM · {room.name}" if room else "ROOM"
        panel = Panel(title, level="info")
        panel.layout(self._inspector_rect)
        panel.draw(surface, ctx)

        if not self._inspector:
            inner = panel.inner(ctx)
            # in focus mode the only empty selection left is a room nobody has
            # pinned a device into
            prompt = "NO DEVICES HERE"
            prompt_px = ctx.font_px(t.size_small)
            if ctx.book.font(prompt_px, mono=True).size(prompt)[0] > inner.width:
                prompt_px = ctx.font_px(t.size_micro)
            blit_text(surface, ctx.book,
                      truncate(ctx.book, prompt, prompt_px, inner.width, mono=True),
                      prompt_px, t.inop, (inner.centerx, inner.centery), anchor="center", mono=True)
            # pointer/keyboard hints are noise on the touch panel, and there is
            # no room for them there anyway
            if ctx.vp.design_width < 700:
                return
            hints = [
                "WHEEL  ZOOM",
                "RIGHT-DRAG  PAN",
                "PGUP/PGDN  FLOOR",
                "F  FIT",
            ]
            size = ctx.font_px(t.size_micro)
            for i, hint in enumerate(reversed(hints)):
                blit_text(surface, ctx.book, hint, size, mix(t.inop, t.background, 0.25),
                          (inner.left, inner.bottom - i * size * 1.5), anchor="bottomleft", mono=True)
            return

        # the master button reports live aggregate state: "3/5 ON"
        if self.showing_zone and self._zone_master is not None:
            on, total = ctx.backend.group_state(self._zone_entity_ids)
            self._zone_master.active = on > 0
            self._zone_master.sub = f"{on}/{total} ON" if total else "NO DEVICES"

        for widget in self._inspector:
            widget.draw(surface, ctx)

        if self.showing_zone:
            zone = self.plan.zone(self.selected_zone)
            if zone is not None and zone.spans_floors:
                inner = panel.inner(ctx)
                blit_text(surface, ctx.book,
                          f"SPANS {len(zone.floor_ids)} FLOORS", ctx.font_px(t.size_micro),
                          t.caution, (inner.left, inner.bottom), anchor="bottomleft", mono=True)
            return

        if self.selected_device:
            entity = ctx.backend.get(self.selected_device)
            inner = panel.inner(ctx)
            size = ctx.font_px(t.size_micro)
            lines = []
            if entity is None:
                lines.append(("ENTITY NOT IN BACKEND", t.caution))
            else:
                lines.append((f"STATE  {entity.state.upper()}", t.text))
                for key in ("brightness", "current_temperature", "temperature", "current_position"):
                    if key in entity.attributes:
                        lines.append((f"{key.upper():<20}{entity.attributes[key]}", t.data))
            for i, (text, colour) in enumerate(lines):
                blit_text(surface, ctx.book,
                          truncate(ctx.book, text, size, inner.width), size, colour,
                          (inner.left, inner.bottom - (len(lines) - 1 - i) * size * 1.5),
                          anchor="bottomleft", mono=True)
