"""Alternative shell for small panels: a drill-down navigator, no drawing.

The stock plan screen puts a scale drawing on the panel and asks the operator
to aim at it.  On the 480x320 target that leaves rooms a few millimetres wide
and the inspector a column barely wider than a slider.  This alternative trades
the spatial sense of the drawing for reach: one thing on screen at a time,
every target at least a fingertip across, and no panning, zooming or
scrolling - what does not fit goes onto a page you turn with a button.

Three stages, one back button:

* **places** - the floor's zones and ungrouped rooms as big cards.  Each card
  carries a power chip, so the common case ("turn the kitchen off") is one tap
  from the home screen and never enters the stage below.
* **place**  - one zone or room: a master toggle, group brightness/target, and
  the devices in it.  A zone with several rooms gets a scope chip row, so a
  member room can still be commanded on its own.
* **device** - one device alone: a toggle sized like a switch, its setter, and
  its attributes.

Every stage owns the whole content rectangle; the shell supplies the tab bar.
Nothing here replaces the stock screens - the shell picks between the two sets
at startup (``--alternative``).
"""

from __future__ import annotations

import pygame

from .. import draw as vd
from ..floorplan.model import Device, Floor
from ..fonts import blit_text, truncate
from ..scaling import Box
from ..theme import mix
from ..ui.base import Pressable, UIContext, Widget
from ..ui.controls import Button, Slider
from ..ui.indicators import ArcGauge, BarGauge, EntityTile, MessageStrip, StatusLamp
from .base import Screen

#: design-unit sizes.  These are pixels on the reference panel, so they are
#: also the numbers a fingertip actually has to hit.
CHIP_H = 32.0
HEAD_H = 40.0
CARD_H = 62.0
ROW_H = 48.0
PAGER_H = 40.0
#: a card narrower than this cannot print a room name beside its power chip
CARD_MIN_W = 150.0
#: a device row narrower than this cannot print a name beside its state
ROW_MIN_W = 140.0
#: Design units shrink with the panel, so on anything below the reference the
#: two-column rule would keep splitting columns that are already too narrow to
#: print a name in. Names are rasterised in real pixels: floor the rule there.
MIN_COL_PX = 130.0


def _columns(width: float, min_w: float, gap: float, limit: int = 2) -> int:
    """Widest column count up to ``limit`` that still pays for its labels.

    Measured in design units *and* in real pixels: design units shrink with
    the panel, so on anything under the reference the unit test alone would
    keep splitting columns already too narrow to print a name in.
    """
    floor = max(min_w, MIN_COL_PX)
    for count in range(limit, 1, -1):
        if width >= floor * count + gap * (count - 1):
            return count
    return 1


def _page_slice(items: list, per_page: int, page: int) -> list:
    return items[page * per_page:][:per_page]


def _fill_rows(area: pygame.Rect, count: int, min_h: float, max_h: float,
               gap: float) -> list[pygame.Rect]:
    """Stack ``count`` rows down ``area``, growing them into the spare space.

    A page holds whole rows, so there is almost always a remainder; handing it
    to the rows rather than leaving it at the bottom is free touch target.
    """
    rows = max(1, count)
    height = min(max_h, max(min_h, (area.height - gap * (rows - 1)) / rows))
    return [pygame.Rect(area.left, round(area.top + index * (height + gap)),
                        area.width, round(height))
            for index in range(rows)]


def _fits(space: float, min_h: float, gap: float) -> int:
    """How many rows of ``min_h`` fit in ``space``; never fewer than one."""
    return max(1, int((space + gap) // (min_h + gap)))


class Chip(Button):
    """Small segmented-control button; a chip row is this UI's tab strip."""

    def __init__(self, label, on_press, *, active: bool = False):
        super().__init__(label, on_press, compact=True)
        self.active = active


class PowerChip(Pressable):
    """Square power target on the right of a place card.

    Laid out after the card, so it is hit-tested first: a tap here toggles the
    place instead of opening it - the one control worth reaching without
    drilling in at all.
    """

    def __init__(self, entity_ids: list[str], on_press):
        super().__init__(on_press)
        self.entity_ids = entity_ids

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        on, total = ctx.backend.group_state(self.entity_ids) if self.entity_ids else (0, 0)
        accent = t.status_color("on" if on else ("off" if total else "inop"))
        cut = ctx.u(t.chamfer * 0.7)
        fill = mix(t.panel_alt, accent, 0.45 if self.is_pressed else (0.28 if on else 0.0))
        vd.chamfer_rect(surface, self.rect, fill=fill, cut=cut)
        vd.chamfer_rect(surface, self.rect, outline=accent if on else t.rule,
                        width=ctx.px(t.stroke), cut=cut)
        # The power glyph is drawn, not typed: it is not in every font stack.
        # Its stroke scales with the chip rather than with the theme's hairline
        # - a 2px ring inside a 40px target reads as a stray scratch, and the
        # polyline arc's joins show at that width.
        radius = min(self.rect.width, self.rect.height) * 0.26
        stroke = max(round(radius * 0.34), ctx.px(t.stroke_bold + 1), 3)
        colour = accent if total else t.inop
        # whole pixels, so the stem cannot land half a pixel off the ring's gap
        centre = (round(self.rect.centerx), round(self.rect.centery + radius * 0.12))
        # the ring's gap is centred on 12 o'clock, where the stem stands: off
        # by even a few degrees and the glyph reads as crooked
        vd.arc(surface, colour, centre, radius, 30.0, 330.0, width=stroke)
        pygame.draw.line(surface, colour, (centre[0], round(centre[1] - radius * 1.35)),
                         (centre[0], round(centre[1] - radius * 0.15)), stroke)


class PlaceCard(Pressable):
    """A zone or an ungrouped room: name, live count, and a way in."""

    def __init__(self, name: str, sub: str, entity_ids: list[str], on_press, *, is_zone: bool):
        super().__init__(on_press)
        self.name = name
        self.sub = sub
        self.entity_ids = entity_ids
        self.is_zone = is_zone
        #: width reserved on the right for the power chip
        self.chip_w = 0.0

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        on, total = ctx.backend.group_state(self.entity_ids) if self.entity_ids else (0, 0)
        accent = t.status_color("on" if on else ("off" if total else "inop"))
        cut = ctx.u(t.chamfer)
        fill = mix(t.panel, t.panel_alt, 1.0 if self.is_pressed else 0.35)
        if on:
            fill = mix(fill, accent, 0.16)
        vd.chamfer_rect(surface, self.rect, fill=fill, cut=cut)
        vd.chamfer_rect(surface, self.rect, outline=accent if on else t.rule,
                        width=ctx.px(t.stroke), cut=cut)
        pygame.draw.rect(surface, accent,
                         pygame.Rect(self.rect.left, self.rect.top, ctx.px(4), self.rect.height))

        pad = ctx.u(10)
        room = max(self.rect.width - pad * 2 - self.chip_w, 1)
        name_px = max(9, min(ctx.font_px(t.size_body), round(self.rect.height * 0.36)))
        sub_px = max(8, min(ctx.font_px(t.size_micro), round(self.rect.height * 0.24)))
        blit_text(surface, ctx.book, truncate(ctx.book, self.name.upper(), name_px, room),
                  name_px, t.text, (self.rect.left + pad, self.rect.top + ctx.u(9)), anchor="topleft")
        state = f"{on}/{total} ON" if total else "NO DEVICES"
        blit_text(surface, ctx.book, truncate(ctx.book, state, sub_px, room), sub_px,
                  accent if total else t.inop,
                  (self.rect.left + pad, self.rect.bottom - ctx.u(9)), anchor="bottomleft", mono=True)
        if self.is_zone:
            blit_text(surface, ctx.book, "ZONE", sub_px, t.data,
                      (self.rect.right - self.chip_w - ctx.u(6), self.rect.top + ctx.u(9)),
                      anchor="topright", mono=True)


class DeviceRow(Pressable):
    """Full-width device row: name, live state, and the whole row as a target."""

    def __init__(self, entity_id: str, label: str, on_press):
        super().__init__(on_press)
        self.entity_id = entity_id
        self.label = label

    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        entity = ctx.backend.get(self.entity_id)
        level = entity.level if entity else "inop"
        accent = t.status_color(level)
        cut = ctx.u(t.chamfer * 0.7)
        vd.chamfer_rect(surface, self.rect,
                        fill=mix(t.panel, t.panel_alt, 1.0 if self.is_pressed else 0.4), cut=cut)
        vd.chamfer_rect(surface, self.rect, outline=t.rule, width=ctx.px(t.stroke), cut=cut)
        pygame.draw.rect(surface, accent,
                         pygame.Rect(self.rect.left, self.rect.top, ctx.px(4), self.rect.height))

        pad = ctx.u(8)
        name_px = max(9, min(ctx.font_px(t.size_small), round(self.rect.height * 0.42)))
        state_px = max(9, min(ctx.font_px(t.size_small), round(self.rect.height * 0.40)))
        state = _entity_state_text(entity)
        name = entity.name if entity else self.label
        # In a three-column grid the name and the state stop fitting on one
        # line: stack them rather than truncate the name to three letters.
        # "LUZ CLOSET SU..." tells the operator nothing about which light it is.
        state_w = ctx.book.font(state_px, mono=True).size(state)[0]
        if state_w > self.rect.width * 0.45:
            blit_text(surface, ctx.book,
                      truncate(ctx.book, name.upper(), name_px, self.rect.width - pad * 2),
                      name_px, t.text, (self.rect.left + pad, self.rect.top + ctx.u(5)),
                      anchor="topleft")
            blit_text(surface, ctx.book, state, state_px, accent,
                      (self.rect.left + pad, self.rect.bottom - ctx.u(5)),
                      anchor="bottomleft", mono=True)
            return
        state_rect = blit_text(surface, ctx.book, state, state_px, accent,
                               (self.rect.right - pad, self.rect.centery),
                               anchor="midright", mono=True)
        room = state_rect.left - self.rect.left - pad * 2
        blit_text(surface, ctx.book, truncate(ctx.book, name.upper(), name_px, max(room, 1)),
                  name_px, t.text, (self.rect.left + pad, self.rect.centery), anchor="midleft")


def _entity_state_text(entity) -> str:
    if entity is None:
        return "MISSING"
    if entity.domain == "sensor":
        return f"{entity.state} {entity.attributes.get('unit_of_measurement', '')}".strip()
    if entity.domain == "light" and entity.is_on and entity.attributes.get("brightness"):
        return f"ON {round(float(entity.attributes['brightness']) / 2.55)}%"
    return entity.state.upper()


class AltHomeScreen(Screen):
    """Places -> place -> device, one stage at a time."""

    key = "plan"
    title = "HOME"
    subtitle = "ROOMS AND DEVICES"

    def __init__(self, app):
        super().__init__(app)
        self.plan = app.plan
        self.floor_id = self.plan.floors[0].id if self.plan.floors else ""
        #: "places" | "place" | "device"
        self.mode = "places"
        self.room_id: str | None = None
        self.zone_id: str | None = None
        #: inside a zone: None commands the whole zone, a room id just that room
        self.scope_room: str | None = None
        self.device_id: str | None = None
        self.page = 0
        self.pages = 1
        self._widgets: list[Widget] = []
        self._head: list[Widget] = []
        self._title = ""
        self._caption = ""
        self._master: Button | None = None
        self._master_ids: list[str] = []
        self.btn_back = Button("< BACK", self._go_back, compact=True)
        self.btn_prev = Button("<", lambda: self._turn(-1), compact=True)
        self.btn_next = Button(">", lambda: self._turn(1), compact=True)

    # -- state -----------------------------------------------------------
    @property
    def floor(self) -> Floor | None:
        return self.plan.floor(self.floor_id)

    def _select_floor(self, floor_id: str) -> None:
        self.floor_id = floor_id
        self.page = 0
        self.mode = "places"
        self.room_id = self.zone_id = self.scope_room = self.device_id = None
        self.invalidate()

    def _open_place(self, room_id: str) -> None:
        zone = self.plan.zone_of(self.floor_id, room_id)
        self.room_id = room_id
        self.zone_id = zone.id if zone else None
        self.scope_room = None
        self.device_id = None
        self.mode = "place"
        self.page = 0
        self.invalidate()

    def _open_device(self, entity_id: str) -> None:
        self.device_id = entity_id
        self.mode = "device"
        self.invalidate()

    def _set_scope(self, room_id: str | None) -> None:
        self.scope_room = room_id
        self.page = 0
        self.invalidate()

    def _go_back(self) -> None:
        if self.mode == "device":
            self.mode = "place"
            self.device_id = None
        else:
            self.mode = "places"
            self.room_id = self.zone_id = self.scope_room = None
        self.page = 0
        self.invalidate()

    def _turn(self, step: int) -> None:
        self.page = max(0, min(self.pages - 1, self.page + step))
        self.invalidate()

    # -- content ---------------------------------------------------------
    def _places(self) -> list[tuple[str, str, str, list[str], bool]]:
        """(name, sub, room_id, entity_ids, is_zone) - one entry per unit of control."""
        floor = self.floor
        if floor is None:
            return []
        units = self.plan.units
        out: list[tuple[str, str, str, list[str], bool]] = []
        seen: set[str] = set()
        for room in floor.rooms:
            zone = self.plan.zone_of(self.floor_id, room.id)
            if zone is None:
                out.append((room.name, f"{room.area:.0f} {units}", room.id,
                            [d.entity_id for d in floor.devices_in(room.id)], False))
            elif zone.id not in seen:
                seen.add(zone.id)
                out.append((zone.name, f"{self.plan.zone_area(zone):.0f} {units}", room.id,
                            [d.entity_id for d in self.plan.zone_devices(zone)], True))
        return out

    def _place_name(self) -> str:
        floor = self.floor
        if self.scope_room and floor is not None:
            room = next((r for r in floor.rooms if r.id == self.scope_room), None)
            if room is not None:
                return room.name
        zone = self.plan.zone(self.zone_id) if self.zone_id else None
        if zone is not None:
            return zone.name
        if floor is not None:
            room = next((r for r in floor.rooms if r.id == self.room_id), None)
            if room is not None:
                return room.name
        return "PLACE"

    def _place_devices(self) -> list[Device]:
        floor = self.floor
        if floor is None:
            return []
        if self.scope_room:
            return floor.devices_in(self.scope_room)
        zone = self.plan.zone(self.zone_id) if self.zone_id else None
        if zone is not None:
            return self.plan.zone_devices(zone)
        return floor.devices_in(self.room_id) if self.room_id else []

    def _device(self) -> Device | None:
        floor = self.floor
        if floor is None or not self.device_id:
            return None
        return next((d for d in floor.devices if d.entity_id == self.device_id), None)

    # -- layout ----------------------------------------------------------
    def layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        gap = ctx.u(ctx.theme.gap)
        self._widgets = []
        self._head = []
        self._master = None
        box = Box(rect).pad(ctx.u(2))

        head_h = max(ctx.u(HEAD_H), 32)
        head, body = box.rows(head_h, box.rect.height - head_h - gap, gap=gap)
        self._head_rect = head.rect
        self._layout_head(ctx, head, gap)

        if self.mode == "places":
            self._layout_places(ctx, body, gap)
        elif self.mode == "place":
            self._layout_place(ctx, body, gap)
        else:
            self._layout_device(ctx, body, gap)

    def _layout_head(self, ctx: UIContext, head: Box, gap: float) -> None:
        """Floor chips on the home stage, a back button everywhere else."""
        if self.mode == "places":
            floors = sorted(self.plan.floors, key=lambda f: -f.level)
            self._title = self.floor.name.upper() if self.floor else "NO PLAN"
            if len(floors) > 1:
                for floor, cell in zip(floors, head.cols(*[1.0] * len(floors), gap=gap * 0.6)):
                    chip = Chip(floor.tag, (lambda i=floor.id: self._select_floor(i)),
                                active=floor.id == self.floor_id)
                    chip.layout(cell.rect)
                    self._head.append(chip)
            return

        back_w = max(ctx.u(84), 70)
        back, rest = head.cols(back_w, head.rect.width - back_w - gap, gap=gap)
        self.btn_back.layout(back.rect)
        self._head.append(self.btn_back)
        self._title = self._place_name().upper()
        if self.mode == "device":
            device = self._device()
            entity = ctx.backend.get(self.device_id) if self.device_id else None
            self._title = (entity.name if entity else
                           (device.display_label if device else self.device_id or "")).upper()
            return

        # a zone is several rooms: keep the scope control the stock inspector has
        zone = self.plan.zone(self.zone_id) if self.zone_id else None
        floor = self.floor
        if zone is None or floor is None:
            return
        member_ids = zone.room_ids_on(self.floor_id)
        members = [r for r in floor.rooms if r.id in member_ids]
        if len(members) < 2:
            return
        scopes: list[tuple[str, str | None]] = [(zone.tag, None)]
        scopes += [(room.name, room.id) for room in members]
        for (label, room_id), cell in zip(scopes, rest.cols(*[1.0] * len(scopes), gap=gap * 0.5)):
            chip = Chip(label, (lambda r=room_id: self._set_scope(r)),
                        active=self.scope_room == room_id)
            chip.layout(cell.rect)
            self._head.append(chip)

    def _layout_pager(self, ctx: UIContext, body: Box, gap: float) -> Box:
        """Take the pager's strip off the bottom of ``body`` and lay it out."""
        pager_h = max(ctx.u(PAGER_H), 34)
        body, pager = body.rows(body.rect.height - pager_h - gap, pager_h, gap=gap)
        prev, nxt = pager.cols(1, 1, gap=gap)
        self.btn_prev.layout(prev.rect)
        self.btn_next.layout(nxt.rect)
        self.btn_prev.visible = self.btn_next.visible = True
        self._widgets += [self.btn_prev, self.btn_next]
        return body

    def _set_pages(self, count: int, per_page: int) -> None:
        self.pages = max(1, -(-count // max(per_page, 1)))
        self.page = min(self.page, self.pages - 1)
        self.btn_prev.enabled = self.page > 0
        self.btn_next.enabled = self.page < self.pages - 1
        if self.pages == 1:
            # the reservation was pessimistic - give the strip back
            self.btn_prev.visible = self.btn_next.visible = False
            self._widgets = [w for w in self._widgets
                             if w is not self.btn_prev and w is not self.btn_next]

    def _layout_places(self, ctx: UIContext, body: Box, gap: float) -> None:
        self.btn_prev.visible = self.btn_next.visible = False
        places = self._places()
        self._caption = f"{len(places)} PLACES"
        if not places:
            self.pages = 1
            return
        cols = _columns(body.rect.width, ctx.u(CARD_MIN_W), gap)
        min_h = max(ctx.u(CARD_H * 0.8), 40)
        rows_needed = -(-len(places) // cols)
        if rows_needed > _fits(body.rect.height, min_h, gap):
            body = self._layout_pager(ctx, body, gap)
        per_page = _fits(body.rect.height, min_h, gap) * cols
        self._set_pages(len(places), per_page)

        page = _page_slice(places, per_page, self.page)
        rows = _fill_rows(body.rect, -(-len(page) // cols), min_h,
                          max(ctx.u(CARD_H * 1.4), 60), gap)
        chip_w = max(ctx.u(ctx.theme.touch_min), 40)
        col_w = (body.rect.width - gap * (cols - 1)) / cols
        for index, (name, sub, room_id, ids, is_zone) in enumerate(page):
            row, col = divmod(index, cols)
            rect = pygame.Rect(round(body.rect.left + col * (col_w + gap)),
                               rows[row].top, round(col_w), rows[row].height)
            card = PlaceCard(name, sub, ids, (lambda r=room_id: self._open_place(r)),
                             is_zone=is_zone)
            card.chip_w = chip_w + ctx.u(8)
            card.layout(rect)
            self._widgets.append(card)
            if ids:
                chip = PowerChip(ids, (lambda i=ids: ctx.backend.toggle_group(i)))
                chip.layout(pygame.Rect(round(rect.right - chip_w - ctx.u(6)),
                                        round(rect.centery - chip_w / 2),
                                        round(chip_w), round(chip_w)))
                self._widgets.append(chip)

    def _layout_place(self, ctx: UIContext, body: Box, gap: float) -> None:
        t = ctx.theme
        backend = ctx.backend
        self.btn_prev.visible = self.btn_next.visible = False
        devices = self._place_devices()
        entity_ids = [d.entity_id for d in devices]
        lights = [i for i in entity_ids if i.startswith("light.")]
        climates = [i for i in entity_ids if i.startswith("climate.")]
        self._caption = f"{len(devices)} DEVICES"

        master_h = max(ctx.u(t.touch_min * 1.15), 44)
        master = Button(self._place_name(), (lambda ids=entity_ids: backend.toggle_group(ids)),
                        level="on", sub=f"{len(entity_ids)} DEVICES")
        master.layout(body.top_slice(master_h).rect)
        self._widgets.append(master)
        self._master = master
        self._master_ids = entity_ids
        body = body.inset(top=master_h + gap)

        # A group slider costs a label line above its track; the device list is
        # the only way to reach a single device, so slider blocks are dropped
        # (climate first) until at least one row - and its pager, if the list
        # needs one - still fits underneath.
        # A slider block is track + its label line above + its tick scale
        # below.  Kept lean on purpose: every unit here comes straight out of
        # the device grid underneath it.
        slider_h = max(ctx.u(28), 24)
        label_h = ctx.u(12)
        tick_h = ctx.u(7)
        min_row = max(ctx.u(ROW_H * 0.7), 30)
        row_gap = gap * 0.6
        pager_h = max(ctx.u(PAGER_H), 34)
        block = slider_h + label_h + tick_h + gap

        # the rows carry a name and a state, nothing else: on a landscape panel
        # two of them sit side by side and the page holds twice as much
        cols = _columns(body.rect.width, ctx.u(ROW_MIN_W), gap, limit=3)

        def list_height(blocks: int) -> float:
            left = body.rect.height - blocks * block
            if len(devices) > _fits(left, min_row, row_gap) * cols:
                left -= pager_h + gap
            return left

        blocks = bool(lights) + bool(climates)
        while blocks and list_height(blocks) < min_row:
            blocks -= 1
        if blocks < 2:
            climates = []
        if blocks < 1:
            lights = []

        for label, unit, ids, commit, extra in (
            ("BRIGHTNESS", "%", lights, backend.set_group_brightness, None),
            ("TARGET", "°C", climates, backend.set_group_temperature, "climate"),
        ):
            if not ids:
                continue
            first = backend.get(ids[0])
            if extra is None:
                on_now, _ = backend.group_state(ids)
                level = float(first.attributes.get("brightness", 0)) / 2.55 if first else 0.0
                slider = Slider(label=label, unit=unit, value=level if on_now else 0.0,
                                on_commit=(lambda v, i=ids, c=commit: c(i, v)))
            else:
                target = (first.number("temperature", 22.0) if first else 22.0) or 22.0
                slider = Slider(minimum=16, maximum=30, step=0.5, value=target, label=label,
                                unit=unit, on_commit=(lambda v, i=ids, c=commit: c(i, v)))
                if first is not None:
                    slider.target = first.number("current_temperature")
            slider.layout(body.inset(top=label_h).top_slice(slider_h).rect)
            self._widgets.append(slider)
            body = body.inset(top=block)

        if not devices or body.rect.height < min_row * 0.8:
            self.pages = 1
            return
        if len(devices) > _fits(body.rect.height, min_row, row_gap) * cols:
            body = self._layout_pager(ctx, body, gap)
        per_page = _fits(body.rect.height, min_row, row_gap) * cols
        self._set_pages(len(devices), per_page)
        page = _page_slice(devices, per_page, self.page)
        lines = _fill_rows(body.rect, -(-len(page) // cols), min_row,
                           max(ctx.u(ROW_H * 1.15), 46), row_gap)
        col_w = (body.rect.width - gap * (cols - 1)) / cols
        for index, device in enumerate(page):
            line, col = divmod(index, cols)
            row = DeviceRow(device.entity_id, device.display_label,
                            (lambda e=device.entity_id: self._open_device(e)))
            row.layout(pygame.Rect(round(body.rect.left + col * (col_w + gap)),
                                   lines[line].top, round(col_w), lines[line].height))
            self._widgets.append(row)

    def _layout_device(self, ctx: UIContext, body: Box, gap: float) -> None:
        t = ctx.theme
        backend = ctx.backend
        self.pages = 1
        self.btn_prev.visible = self.btn_next.visible = False
        device = self._device()
        if device is None or self.device_id is None:
            self._caption = ""
            return
        entity = backend.get(self.device_id)
        self._caption = self.device_id.upper()

        toggle_h = max(ctx.u(t.touch_min * 1.6), 56)
        toggle = Button(entity.name if entity else device.display_label,
                        (lambda e=self.device_id: backend.toggle(e)),
                        level="on", sub=self.device_id)
        toggle.layout(body.top_slice(toggle_h).rect)
        self._widgets.append(toggle)
        self._master = toggle
        self._master_ids = [self.device_id]
        body = body.inset(top=toggle_h + gap)

        slider_h = max(ctx.u(38), 28)
        kind = device.resolved_kind
        slider = None
        if kind == "light":
            level = float(entity.attributes.get("brightness", 0)) / 2.55 if entity else 0.0
            slider = Slider(label="BRIGHTNESS", unit="%", value=level,
                            on_commit=(lambda v, e=self.device_id: backend.set_brightness(e, v)))
        elif kind == "climate" and entity is not None:
            target = entity.number("temperature", 22.0) or 22.0
            slider = Slider(minimum=16, maximum=30, step=0.5, value=target,
                            label="TARGET", unit="°C",
                            on_commit=(lambda v, e=self.device_id: backend.set_temperature(e, v)))
            slider.target = entity.number("current_temperature")
        elif kind == "cover" and entity is not None:
            position = entity.number("current_position", 100.0) or 0.0
            slider = Slider(label="POSITION", unit="%", value=position,
                            on_commit=(lambda v, e=self.device_id: backend.set_cover_position(e, v)))
        if slider is not None and body.rect.height > slider_h * 2:
            slider.layout(body.inset(top=ctx.u(14)).top_slice(slider_h).rect)
            self._widgets.append(slider)

    # -- events ----------------------------------------------------------
    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        for widget in reversed(self._head + self._widgets):
            if widget.visible and widget.enabled and widget.handle(event, ctx):
                return True
        if event.type != pygame.KEYDOWN:
            return False
        # not ESC: the shell claims that one for quit
        if event.key == pygame.K_BACKSPACE and self.mode != "places":
            self._go_back()
            return True
        if event.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN) and self.mode == "places":
            step = 1 if event.key == pygame.K_PAGEUP else -1
            index = self.plan.index_of(self.floor_id) + step
            if 0 <= index < len(self.plan.floors):
                self._select_floor(self.plan.floors[index].id)
            return True
        if event.key in (pygame.K_LEFT, pygame.K_RIGHT) and self.pages > 1:
            self._turn(-1 if event.key == pygame.K_LEFT else 1)
            return True
        return False

    # -- drawing ---------------------------------------------------------
    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        # live aggregate on the master button, the way the stock inspector does
        if self._master is not None and self.mode in ("place", "device"):
            on, total = ctx.backend.group_state(self._master_ids)
            self._master.active = on > 0
            self._master.sub = f"{on}/{total} ON" if total else "NO DEVICES"

        for widget in self._head + self._widgets:
            if widget.visible:
                widget.draw(surface, ctx)

        # The head row carries chips only when there is something to switch
        # between; whatever it leaves free gets the stage's name and count.
        head = getattr(self, "_head_rect", None)
        chips = [w for w in self._head if isinstance(w, Chip)]
        if head is not None and not chips:
            left = self.btn_back.rect.right + ctx.u(10) if self.mode != "places" else head.left + ctx.u(6)
            caption = self._caption
            if self.pages > 1:
                caption = f"{caption}  ·  PAGE {self.page + 1}/{self.pages}"
            cap_px = ctx.font_px(t.size_micro)
            cap_rect = blit_text(surface, ctx.book, caption, cap_px, t.inop,
                                 (head.right - ctx.u(6), head.centery),
                                 anchor="midright", mono=True) if caption else None
            room = (cap_rect.left if cap_rect else head.right) - left - ctx.u(8)
            title_px = ctx.font_px(t.size_body)
            blit_text(surface, ctx.book,
                      truncate(ctx.book, self._title, title_px, max(room, 1)),
                      title_px, t.text, (left, head.centery), anchor="midleft")
        if self.mode == "places" and not self._widgets:
            blit_text(surface, ctx.book, "NO PLACES ON THIS FLOOR", ctx.font_px(t.size_small),
                      t.caution, self.rect.center, anchor="center", mono=True)
        if self.mode == "device":
            self._draw_attributes(surface, ctx)

    def _draw_attributes(self, surface: pygame.Surface, ctx: UIContext) -> None:
        t = ctx.theme
        entity = ctx.backend.get(self.device_id) if self.device_id else None
        size = ctx.font_px(t.size_small)
        lines: list[tuple[str, tuple[int, int, int]]] = []
        if entity is None:
            lines.append(("ENTITY NOT IN BACKEND", t.caution))
        else:
            lines.append((f"STATE  {entity.state.upper()}", t.text))
            for key in ("brightness", "current_temperature", "temperature", "current_position"):
                if key in entity.attributes:
                    lines.append((f"{key.upper():<20}{entity.attributes[key]}", t.data))
        for i, (text, colour) in enumerate(reversed(lines)):
            blit_text(surface, ctx.book,
                      truncate(ctx.book, text, size, self.rect.width - ctx.u(16), mono=True),
                      size, colour,
                      (self.rect.left + ctx.u(8), self.rect.bottom - ctx.u(6) - i * size * 1.5),
                      anchor="bottomleft", mono=True)


class AltVitalsScreen(Screen):
    """Vitals, quick controls and status as three full-screen sections.

    The stock overview puts all three side by side; below about 200 design
    units per column that is three unreadable slivers.  Here a chip row picks
    one section and it gets the whole rectangle.
    """

    key = "overview"
    title = "VITALS"
    subtitle = "HOUSE STATUS SUMMARY"

    SECTIONS = (("vitals", "VITALS"), ("quick", "QUICK"), ("status", "STATUS"))

    def __init__(self, app):
        super().__init__(app)
        from .overview import DEFAULT_GAUGES

        config = dict(getattr(app, "config", {}).get("overview") or {})
        self.gauge_specs = config.get("gauges") or DEFAULT_GAUGES
        self.bar_specs = config.get("bars") or []
        self.quick_ids: list[str] = config.get("quick_controls") or []
        self.lamp_specs = config.get("lamps") or []
        self.section = "vitals"
        self.page = 0
        self.pages = 1
        self.gauges: list[tuple[str, ArcGauge]] = []
        self.bars: list[tuple[str, BarGauge]] = []
        self.tiles: list[EntityTile] = []
        self.lamps: list[StatusLamp] = []
        self._lamp_ids: list[str] = []
        self.messages = MessageStrip(max_lines=6)
        self._chips: list[Chip] = []
        self.btn_prev = Button("<", lambda: self._turn(-1), compact=True)
        self.btn_next = Button(">", lambda: self._turn(1), compact=True)

    def _select(self, section: str) -> None:
        self.section = section
        self.page = 0
        self.invalidate()

    def _turn(self, step: int) -> None:
        self.page = max(0, min(self.pages - 1, self.page + step))
        self.invalidate()

    # -- discovery (same rules as the stock overview) --------------------
    def _quick_entities(self, ctx: UIContext) -> list[str]:
        if self.quick_ids:
            return self.quick_ids
        entities = sorted(ctx.backend.by_domain("light", "switch", "fan"), key=lambda e: e.entity_id)
        return [e.entity_id for e in entities[:12]]

    def _lamp_entities(self, ctx: UIContext) -> list[dict]:
        if self.lamp_specs:
            return self.lamp_specs
        entities = sorted(ctx.backend.by_domain("binary_sensor", "lock", "alarm_control_panel"),
                          key=lambda e: e.entity_id)
        return [{"entity_id": e.entity_id, "label": e.name} for e in entities[:8]]

    # -- layout ----------------------------------------------------------
    def layout(self, rect: pygame.Rect, ctx: UIContext) -> None:
        gap = ctx.u(ctx.theme.gap)
        box = Box(rect).pad(ctx.u(2))
        chip_h = max(ctx.u(CHIP_H), 28)
        head, body = box.rows(chip_h, box.rect.height - chip_h - gap, gap=gap)

        self._chips = []
        for (key, label), cell in zip(self.SECTIONS, head.cols(1, 1, 1, gap=gap * 0.6)):
            chip = Chip(label, (lambda k=key: self._select(k)), active=key == self.section)
            chip.layout(cell.rect)
            self._chips.append(chip)

        self.gauges = []
        self.bars = []
        self.tiles = []
        self.lamps = []
        self.btn_prev.visible = self.btn_next.visible = False
        if self.section == "vitals":
            self._layout_vitals(ctx, body, gap)
        elif self.section == "quick":
            self._layout_quick(ctx, body, gap)
        else:
            self._layout_status(ctx, body, gap)

    def _layout_vitals(self, ctx: UIContext, body: Box, gap: float) -> None:
        bar_h = max(ctx.u(34), 26)
        gauge_box = body
        if self.bar_specs:
            strip = bar_h * len(self.bar_specs) + gap * (len(self.bar_specs) - 1)
            gauge_box, bar_box = body.rows(max(body.rect.height - strip - gap, 1), strip, gap=gap)
            for index, spec in enumerate(self.bar_specs):
                bar = BarGauge(spec.get("label", spec["entity_id"]),
                               minimum=float(spec.get("min", 0)),
                               maximum=float(spec.get("max", 100)),
                               unit=spec.get("unit", ""))
                bar.caution_above = spec.get("caution_above")
                bar.layout(pygame.Rect(bar_box.rect.left,
                                       round(bar_box.rect.top + index * (bar_h + gap)),
                                       bar_box.rect.width, round(bar_h)))
                self.bars.append((spec["entity_id"], bar))
        if not self.gauge_specs:
            return
        for spec, cell in zip(self.gauge_specs,
                              gauge_box.cols(*[1.0] * len(self.gauge_specs), gap=gap)):
            gauge = ArcGauge(spec.get("label", spec["entity_id"]),
                             minimum=float(spec.get("min", 0)),
                             maximum=float(spec.get("max", 100)),
                             unit=spec.get("unit", ""))
            gauge.caution_above = spec.get("caution_above")
            gauge.warning_above = spec.get("warning_above")
            gauge.layout(cell.rect)
            self.gauges.append((spec["entity_id"], gauge))

    def _layout_quick(self, ctx: UIContext, body: Box, gap: float) -> None:
        ids = self._quick_entities(ctx)
        if not ids:
            self.pages = 1
            return
        min_h = max(ctx.u(ROW_H * 0.75), 30)
        cols = _columns(body.rect.width, ctx.u(CARD_MIN_W), gap)
        if len(ids) > _fits(body.rect.height, min_h, gap) * cols:
            pager_h = max(ctx.u(PAGER_H), 34)
            body, pager = body.rows(body.rect.height - pager_h - gap, pager_h, gap=gap)
            prev, nxt = pager.cols(1, 1, gap=gap)
            self.btn_prev.layout(prev.rect)
            self.btn_next.layout(nxt.rect)
            self.btn_prev.visible = self.btn_next.visible = True
        per_page = _fits(body.rect.height, min_h, gap) * cols
        self.pages = max(1, -(-len(ids) // per_page))
        self.page = min(self.page, self.pages - 1)
        self.btn_prev.enabled = self.page > 0
        self.btn_next.enabled = self.page < self.pages - 1
        page = _page_slice(ids, per_page, self.page)
        rows = _fill_rows(body.rect, -(-len(page) // cols), min_h,
                          max(ctx.u(ROW_H * 1.4), 56), gap)
        col_w = (body.rect.width - gap * (cols - 1)) / cols
        for index, entity_id in enumerate(page):
            row, col = divmod(index, cols)
            tile = EntityTile(entity_id)
            tile.layout(pygame.Rect(round(body.rect.left + col * (col_w + gap)),
                                    rows[row].top, round(col_w), rows[row].height))
            self.tiles.append(tile)

    def _layout_status(self, ctx: UIContext, body: Box, gap: float) -> None:
        specs = self._lamp_entities(ctx)
        lamp_h = max(ctx.u(26), 20)
        lamps_h = min(lamp_h * len(specs), body.rect.height * 0.6)
        lamp_box, msg_box = body.rows(max(lamps_h, 1),
                                      max(body.rect.height - lamps_h - gap, 1), gap=gap)
        self._lamp_ids = [spec["entity_id"] for spec in specs]
        for index, spec in enumerate(specs):
            lamp = StatusLamp(spec.get("label", spec["entity_id"]))
            lamp.layout(pygame.Rect(lamp_box.rect.left, round(lamp_box.rect.top + index * lamp_h),
                                    lamp_box.rect.width, round(lamp_h)))
            self.lamps.append(lamp)
        self.messages.layout(msg_box.rect)

    # -- events ----------------------------------------------------------
    def handle(self, event: pygame.event.Event, ctx: UIContext) -> bool:
        widgets: list[Widget] = [*self._chips, *self.tiles]
        if self.btn_prev.visible:
            widgets += [self.btn_prev, self.btn_next]
        for widget in reversed(widgets):
            if widget.visible and widget.enabled and widget.handle(event, ctx):
                return True
        return False

    # -- drawing ---------------------------------------------------------
    def draw(self, surface: pygame.Surface, ctx: UIContext) -> None:
        for chip in self._chips:
            chip.draw(surface, ctx)
        for entity_id, gauge in self.gauges:
            entity = ctx.backend.get(entity_id)
            gauge.value = entity.number("state") if entity else None
            gauge.draw(surface, ctx)
        for entity_id, bar in self.bars:
            entity = ctx.backend.get(entity_id)
            bar.value = entity.number("state") if entity else None
            bar.draw(surface, ctx)
        for tile in self.tiles:
            tile.draw(surface, ctx)
        for entity_id, lamp in zip(self._lamp_ids, self.lamps):
            entity = ctx.backend.get(entity_id)
            lamp.level = entity.level if entity else "inop"
            lamp.draw(surface, ctx)
        if self.section == "status":
            self.messages.lines = [(a.level, a.text) for a in ctx.backend.alerts()]
            self.messages.draw(surface, ctx)
        for button in (self.btn_prev, self.btn_next):
            if button.visible:
                button.draw(surface, ctx)
