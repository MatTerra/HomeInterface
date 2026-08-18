"""Draw a :class:`Floor` into a rectangle, at any size.

The view is a fit-to-rect transform with optional zoom/pan.  Every floor of a
plan shares the *whole plan's* bounding box by default, so switching storeys
does not make the drawing jump or rescale - the same trick a real
multi-storey site plan uses.
"""

from __future__ import annotations

import math

import pygame

from .. import draw as vd
from ..fonts import FontBook, blit_text, truncate
from ..scaling import Viewport
from ..theme import RGB, Theme, mix
from .model import BBox, Device, Floor, Point, Room

#: nominal device marker radius, in plan units (metres) - about the footprint
#: of a light fitting, so markers read as objects in the room rather than as
#: overlay pins that scale with the screen
MARKER_RADIUS_M = 0.42

#: wall thickness in plan units, used to build partitions out of the room
#: outlines - rooms carry their footprint but not the wall around it
WALL_THICKNESS = 0.15


def _inside(point: Point, box: BBox) -> bool:
    return box.min_x <= point[0] <= box.max_x and box.min_y <= point[1] <= box.max_y


def device_icon(
    surface: pygame.Surface,
    kind: str,
    center: tuple[float, float],
    radius: float,
    colour: RGB,
    width: int,
) -> None:
    """Draw a device pictogram as vectors.

    Deliberately not text: no system font ships a consistent set of these
    symbols, and glyph fallback produces tofu boxes on a panel that must never
    show one.
    """
    cx, cy = center
    r = radius

    if kind == "light":
        pygame.draw.polygon(surface, colour, [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], width)
        pygame.draw.circle(surface, colour, (round(cx), round(cy)), max(1, round(r * 0.28)))
    elif kind in ("switch", "input_boolean"):
        box = pygame.Rect(0, 0, round(r * 1.7), round(r * 1.7))
        box.center = (round(cx), round(cy))
        pygame.draw.rect(surface, colour, box, width)
        pygame.draw.line(surface, colour, (cx, cy - r * 0.5), (cx, cy + r * 0.5), width)
    elif kind == "fan":
        for k in range(3):
            angle = k * 120.0
            pygame.draw.line(surface, colour, center, vd.radial_point(center, r, angle), width)
        pygame.draw.circle(surface, colour, (round(cx), round(cy)), max(1, round(r * 0.22)))
    elif kind == "climate":
        for k in range(3):
            y = cy + (k - 1) * r * 0.6
            pygame.draw.line(surface, colour, (cx - r * 0.8, y), (cx + r * 0.8, y), width)
    elif kind == "sensor":
        pygame.draw.circle(surface, colour, (round(cx), round(cy)), max(2, round(r * 0.85)), width)
        pygame.draw.circle(surface, colour, (round(cx), round(cy)), max(1, round(r * 0.22)))
    elif kind == "binary_sensor":
        pygame.draw.polygon(surface, colour,
                            [(cx, cy - r), (cx + r * 0.9, cy + r * 0.7), (cx - r * 0.9, cy + r * 0.7)], width)
    elif kind == "cover":
        box = pygame.Rect(0, 0, round(r * 1.8), round(r * 1.6))
        box.center = (round(cx), round(cy))
        pygame.draw.rect(surface, colour, box, width)
        for k in range(1, 3):
            y = box.top + box.height * k / 3
            pygame.draw.line(surface, colour, (box.left, y), (box.right, y), width)
    elif kind == "lock":
        body = pygame.Rect(0, 0, round(r * 1.6), round(r * 1.1))
        body.center = (round(cx), round(cy + r * 0.35))
        pygame.draw.rect(surface, colour, body, width)
        vd.arc(surface, colour, (cx, body.top), r * 0.55, -90.0, 90.0, width=width)
    elif kind == "camera":
        pygame.draw.circle(surface, colour, (round(cx), round(cy)), max(2, round(r * 0.8)), width)
        pygame.draw.line(surface, colour, (cx, cy - r * 0.8), (cx, cy + r * 0.8), width)
    elif kind == "media_player":
        pygame.draw.polygon(surface, colour,
                            [(cx - r * 0.5, cy - r * 0.8), (cx + r * 0.8, cy), (cx - r * 0.5, cy + r * 0.8)], width)
    else:
        pygame.draw.circle(surface, colour, (round(cx), round(cy)), max(2, round(r * 0.6)))


class PlanView:
    """Maps plan coordinates to pixels inside ``rect``."""

    def __init__(
        self,
        bbox: BBox,
        rect: pygame.Rect,
        *,
        margin: float = 0.06,
        zoom: float = 1.0,
        pan: Point = (0.0, 0.0),
    ) -> None:
        self.bbox = bbox
        self.rect = pygame.Rect(rect)
        self.zoom = max(0.2, min(8.0, zoom))
        self.pan = pan
        usable_w = self.rect.width * (1 - 2 * margin)
        usable_h = self.rect.height * (1 - 2 * margin)
        span_x = max(bbox.width, 1e-6)
        span_y = max(bbox.height, 1e-6)
        self.scale = min(usable_w / span_x, usable_h / span_y) * self.zoom
        cx, cy = bbox.center
        self._origin_x = self.rect.centerx - (cx + pan[0]) * self.scale
        self._origin_y = self.rect.centery - (cy + pan[1]) * self.scale

    def to_screen(self, point: Point) -> tuple[float, float]:
        return (self._origin_x + point[0] * self.scale, self._origin_y + point[1] * self.scale)

    def to_plan(self, pixel: tuple[float, float]) -> Point:
        return ((pixel[0] - self._origin_x) / self.scale, (pixel[1] - self._origin_y) / self.scale)

    def poly(self, points: list[Point]) -> list[tuple[float, float]]:
        return [self.to_screen(p) for p in points]

    def length(self, plan_length: float) -> float:
        return plan_length * self.scale


class FloorRenderer:
    def __init__(self, theme: Theme, book: FontBook):
        self.theme = theme
        self.book = book
        #: single-entry cache of the static layer: (key, surface)
        self._static_cache: tuple[tuple, pygame.Surface] | None = None

    def invalidate(self) -> None:
        """Drop the cached static layer (call on resize)."""
        self._static_cache = None

    # -- main entry ------------------------------------------------------
    def render(
        self,
        surface: pygame.Surface,
        floor: Floor,
        view: PlanView,
        vp: Viewport,
        *,
        states: dict[str, str] | None = None,
        selected_room: str | None = None,
        selected_device: str | None = None,
        hover_device: str | None = None,
        zone_rooms: frozenset[str] | None = None,
        show_grid: bool = True,
        units: str = "m",
        devices: list[Device] | None = None,
        visible_rooms: frozenset[str] | None = None,
        room_labels: dict[str, str | None] | None = None,
        show_area: bool = True,
        label_sizes: tuple[float, ...] | None = None,
        marker_max_u: float = 13.0,
    ) -> None:
        states = states or {}
        shown = floor.devices if devices is None else devices
        clip = surface.get_clip()
        surface.set_clip(view.rect)

        static = self._static_layer(
            floor, view, vp, selected_room, zone_rooms or frozenset(), show_grid, units,
            shown, visible_rooms, room_labels or {}, show_area, label_sizes,
            marker_max_u,
        )
        surface.blit(static, view.rect.topleft)

        for device in shown:
            self._device(
                surface, device, view, vp,
                level=states.get(device.entity_id, "inop"),
                selected=device.entity_id == selected_device,
                hover=device.entity_id == hover_device,
                max_u=marker_max_u,
            )

        surface.set_clip(clip)

    # -- static layer (grid, rooms, walls, openings, labels, scale bar) --
    def _static_layer(
        self,
        floor: Floor,
        view: PlanView,
        vp: Viewport,
        selected_room: str | None,
        zone_rooms: frozenset[str],
        show_grid: bool,
        units: str,
        devices: list[Device],
        visible_rooms: frozenset[str] | None,
        room_labels: dict[str, str | None],
        show_area: bool,
        label_sizes: tuple[float, ...] | None,
        marker_max_u: float,
    ) -> pygame.Surface:
        key = (
            floor.id,
            (view.rect.width, view.rect.height),
            round(view.scale, 4),
            round(view.zoom, 4),
            (round(view.pan[0], 4), round(view.pan[1], 4)),
            selected_room,
            zone_rooms,
            show_grid,
            units,
            tuple(d.entity_id for d in devices),
            visible_rooms,
            tuple(sorted(room_labels.items())),
            show_area,
            label_sizes,
            marker_max_u,
        )
        if self._static_cache is not None and self._static_cache[0] == key:
            return self._static_cache[1]

        # draw at surface-local coordinates: a PlanView identical to ``view``
        # except translated so its rect starts at the origin, which shifts
        # every to_screen() output by -view.rect.topleft.
        local_rect = pygame.Rect(0, 0, view.rect.width, view.rect.height)
        shifted = PlanView.__new__(PlanView)
        shifted.bbox = view.bbox
        shifted.rect = local_rect
        shifted.zoom = view.zoom
        shifted.pan = view.pan
        shifted.scale = view.scale
        shifted._origin_x = view._origin_x - view.rect.left
        shifted._origin_y = view._origin_y - view.rect.top

        layer = pygame.Surface(local_rect.size, pygame.SRCALPHA)
        t = self.theme

        if show_grid:
            spacing = shifted.length(1.0)
            while 0 < spacing < vp.u(28):
                spacing *= 2  # keep the lattice readable when zoomed out
            vd.hairline_grid(layer, local_rect, t.rule, spacing=spacing, alpha=26)

        # In focus mode only the room (or the zone's rooms) is drawn: the rest
        # of the storey is not dimmed but genuinely absent, so a single room
        # gets the whole rectangle and the devices in it are finally legible.
        rooms = [r for r in floor.rooms if visible_rooms is None or r.id in visible_rooms]
        scope = self._scope_bbox(rooms) if visible_rooms is not None else None

        for room in rooms:
            self._room(layer, room, shifted, vp,
                       selected=room.id == selected_room, in_zone=room.id in zone_rooms)
        # Walls first from the rooms, then the explicit ones: the ``walls``
        # block is for anything the rooms cannot express, such as the free-
        # standing sides of the balcony.
        self._partitions(layer, rooms, shifted, vp)
        for wall in floor.walls:
            mid = ((wall.a[0] + wall.b[0]) / 2, (wall.a[1] + wall.b[1]) / 2)
            if scope is None or _inside(mid, scope):
                self._wall(layer, wall, shifted, vp)
        # Selection and zone rings go on top of the walls, or the wall pass
        # would paint over the very cue the operator is looking for.
        for room in rooms:
            if room.id == selected_room:
                self._room_edge(layer, room, shifted, vp)
        if zone_rooms:
            self._zone_outline(layer, rooms, shifted, vp, zone_rooms, t.data)
        for opening in floor.openings:
            if scope is None or _inside(opening.at, scope):
                self._opening(layer, opening, shifted, vp)
        markers = self._marker_rects(devices, shifted, vp, marker_max_u)
        for room in rooms:
            label = room_labels.get(room.id, room.name)
            if label is None:
                continue
            self._room_label(layer, room, shifted, vp, units, markers,
                             text=label, show_area=show_area, sizes=label_sizes)

        self._scale_bar(layer, shifted, vp, units)

        self._static_cache = (key, layer)
        return layer

    # -- pieces ----------------------------------------------------------
    def _room_tint(self, room: Room, selected: bool, in_zone: bool = False) -> RGB:
        t = self.theme
        base = mix(t.panel, t.panel_alt, 0.5)
        if selected:
            return mix(base, t.data, 0.28)
        if in_zone:
            # lighter than the single-room selection, so the operator can see
            # at a glance that the command will reach more than one room
            return mix(base, t.data, 0.16)
        if room.kind in ("outdoor", "garden", "balcony", "terrace"):
            return mix(base, t.normal, 0.10)
        if room.kind in ("service", "garage", "utility", "storage"):
            return mix(base, t.inop, 0.20)
        if room.kind in ("wet", "bathroom", "laundry"):
            return mix(base, t.data, 0.10)
        return base

    def _room(self, surface, room: Room, view: PlanView, vp: Viewport, *,
              selected: bool, in_zone: bool = False) -> None:
        pts = view.poly(room.polygon)
        if len(pts) < 3:
            return
        pygame.draw.polygon(surface, self._room_tint(room, selected, in_zone), pts)

    @staticmethod
    def _scope_bbox(rooms: list[Room]) -> BBox | None:
        """Union of the drawn rooms, slackened by a wall's worth of margin.

        Used to decide which explicit walls and which openings still belong to
        the picture once the rest of the storey is gone.
        """
        if not rooms:
            return None
        box = rooms[0].bbox
        for room in rooms[1:]:
            box = box.merged(room.bbox)
        return box.expanded(WALL_THICKNESS * 3)

    def _partitions(self, surface, rooms: list[Room], view: PlanView, vp: Viewport) -> None:
        """Build every wall out of the rooms themselves.

        A plan's walls are the negative space between its rooms, so deriving
        them beats listing them: the ``walls`` block only ever held a handful
        of segments, and every partition nobody remembered to write - between
        the bedroom and its bathroom, say - came out as a hole with the page
        showing through.

        Each room draws its own outline at full wall thickness, centred on its
        boundary.  Two rooms sharing a 0.15 gap therefore cover half of it
        each and meet exactly in the middle, and a room on the facade lays down
        the inner half of the exterior wall.
        """
        width = max(vp.px(self.theme.stroke_bold), int(view.length(WALL_THICKNESS)))
        for room in rooms:
            pts = view.poly(room.polygon)
            if len(pts) >= 3:
                pygame.draw.polygon(surface, self._wall_colour, pts, width)

    def _room_edge(self, surface, room: Room, view: PlanView, vp: Viewport) -> None:
        """Selection ring, drawn after the walls so it is not buried by them."""
        pts = view.poly(room.polygon)
        if len(pts) >= 3:
            pygame.draw.polygon(surface, self.theme.data, pts, vp.px(self.theme.stroke_bold))

    def _zone_outline(self, surface, rooms: list[Room], view: PlanView, vp: Viewport,
                      zone_rooms: frozenset[str], colour: RGB) -> None:
        """Dashed ring around each room of the active zone.

        Drawn per room rather than as a true union outline: computing the
        merged boundary of arbitrary polygons is a lot of machinery for a cue
        that reads just as well as a dashed edge on every member.
        """
        t = self.theme
        for room in rooms:
            if room.id not in zone_rooms:
                continue
            pts = view.poly(room.polygon)
            if len(pts) < 3:
                continue
            for i in range(len(pts)):
                vd.dashed_line(surface, colour, pts[i], pts[(i + 1) % len(pts)],
                               width=vp.px(t.stroke_bold), dash=vp.u(5), gap=vp.u(4))

    @property
    def _wall_colour(self) -> RGB:
        """Structure, not information.

        Walls used to be drawn in ``theme.text`` - the same white as a door -
        so the one thing on the plan you actually operate through was invisible
        against the thing that merely holds the roof up.  They now sit between
        the room outline and white: still clearly the strongest structural
        line, but no longer competing with the openings.
        """
        return mix(self.theme.rule_bright, self.theme.text, 0.35)

    def _wall(self, surface, wall, view: PlanView, vp: Viewport) -> None:
        width = max(vp.px(self.theme.stroke_bold), int(view.length(wall.thickness)))
        pygame.draw.line(surface, self._wall_colour, view.to_screen(wall.a), view.to_screen(wall.b), width)

    def _opening(self, surface, opening, view: PlanView, vp: Viewport) -> None:
        """Draw the gap an opening makes in its wall - and nothing else.

        Doors used to get a quarter-circle swing arc as well.  On a control
        panel that arc is a liability: it is drawn at room scale, so on a small
        display it sweeps across labels and device markers, and it asserts
        which way a door opens - a detail this plan is not a reliable source
        for.  ``Opening.swing`` is still carried in the model for anyone who
        wants to draw it, it just is not drawn here.
        """
        t = self.theme
        half = view.length(opening.width) / 2.0
        cx, cy = view.to_screen(opening.at)
        rad = math.radians(opening.angle)
        dx, dy = math.cos(rad) * half, math.sin(rad) * half
        a = (cx - dx, cy - dy)
        b = (cx + dx, cy + dy)

        if opening.kind != "window":
            # White is reserved for the openings you walk through, and now that
            # walls are dimmer it is the brightest ink on the plan.
            pygame.draw.line(surface, t.text, a, b, vp.px(t.stroke_bold))
            return

        # Glazing, drawn as the usual pair of thin parallel lines.  Grey rather
        # than cyan on purpose: cyan is the data colour, and a window is about
        # to become state-bearing (open/closed contact).  Grey reads as "no
        # sensor yet" - what ``inop`` means everywhere else - which leaves the
        # semantic colours free for the state once it exists.
        nx, ny = -math.sin(rad), math.cos(rad)
        offset = vp.px(t.stroke_bold) * 0.85
        for side in (-1, 1):
            ox, oy = nx * offset * side, ny * offset * side
            pygame.draw.line(
                surface, t.inop, (a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy), vp.px(t.stroke)
            )

    def _marker_radius(self, view: PlanView, vp: Viewport, max_u: float = 13.0) -> float:
        """Marker radius in pixels; kept in one place so labels can dodge them."""
        return max(vp.u(6.0), min(view.length(MARKER_RADIUS_M), vp.u(max_u)))

    def _marker_rects(self, devices: list[Device], view: PlanView, vp: Viewport,
                      max_u: float = 13.0) -> list[pygame.Rect]:
        r = self._marker_radius(view, vp, max_u) * 1.25  # the selected/hover size, so
        side = round(r * 2)                              # layout does not shift on hover
        out = []
        for device in devices:
            cx, cy = view.to_screen(device.at)
            rect = pygame.Rect(0, 0, side, side)
            rect.center = (round(cx), round(cy))
            out.append(rect)
        return out

    @staticmethod
    def _dodge(lines: list[pygame.Rect], markers: list[pygame.Rect],
               low: float, high: float) -> int | None:
        """Vertical shift that moves every line clear of every marker.

        Labels sit at the centre of a room and so do the fittings they name,
        so the two collide constantly.  Moving the text is the right way round:
        a marker is a touch target at a real position, the label is not.

        Collision is tested against the text lines themselves, never against
        their bounding block - the leading between name and area is exactly
        where a marker can sit harmlessly, and treating that gap as occupied
        would shove labels around (or cost them their area line) for an overlap
        that does not exist.

        Returns 0 when the anchor is already clear, the smallest non-zero shift
        that works, or None when nothing inside ``low``..``high`` is clear.
        """
        if not lines:
            return 0
        top = min(line.top for line in lines)
        bottom = max(line.bottom for line in lines)

        # Butting a line right up against a marker edge is exactly clear -
        # pygame rects touching do not collide - and a room is routinely short
        # by the pixel a safety margin would cost it.
        shifts = [0]
        for marker in markers:
            for line in lines:
                shifts.append(marker.top - line.bottom)
                shifts.append(marker.bottom - line.top)

        best = None
        for shift in shifts:
            if top + shift < low or bottom + shift > high:
                continue
            if any(line.move(0, shift).colliderect(m) for line in lines for m in markers):
                continue
            if best is None or abs(shift) < abs(best):
                best = shift
        return best

    def _room_label(self, surface, room: Room, view: PlanView, vp: Viewport, units: str,
                    markers: list[pygame.Rect] | None = None, *, text: str | None = None,
                    show_area: bool = True, sizes: tuple[float, ...] | None = None) -> None:
        cx, cy = view.to_screen(room.centroid)
        # The free span through the anchor, not the bounding box: an S-shaped
        # bedroom or an L-shaped kitchen would otherwise be told it has the
        # width of its widest slice and print text across the party wall.
        free_w, free_h = room.label_extent
        box_w = view.length(free_w) * 0.92
        box_h = view.length(free_h) * 0.9

        # Step the type down before resorting to an ellipsis: a narrow room
        # labelled "COZINHA" in small type beats one labelled "COZ...".  The
        # fit test is against the room's own size, never against an ink-scaled
        # constant - that made big displays drop labels small ones kept.
        t = self.theme
        name = (text if text is not None else room.name).upper()
        name_px = 0
        for size in (sizes or (t.size_small, t.size_micro)):
            candidate = vp.font_px(size)
            if candidate > box_h * 0.55:
                continue
            name_px = candidate
            if self.book.font(candidate).size(name)[0] <= box_w:
                break
        if not name_px:
            return  # genuinely no room for type; the outline carries it

        name = truncate(self.book, name, name_px, box_w)
        if name in ("", "…"):
            return

        # Decide the area line before drawing anything: the block has to be
        # measured whole so it can be moved clear of a marker as one piece.
        area = ""
        area_px = vp.font_px(self.theme.size_micro)
        gap = vp.u(3)
        if show_area and room.area > 0 and box_h > (name_px + area_px) * 1.6:
            text = f"{room.area:.1f} {units}²"
            # Never truncate a measurement - "12.5 m…" is a worse label than no
            # label - so an area that does not fit is simply dropped. The name
            # carries the room on its own.
            if self.book.font(area_px, mono=True).size(text)[0] <= box_w:
                area = text

        # Dodge inside the room's true free height, not the shrunk text budget:
        # this is about clearing a marker, not about margins.
        span = view.length(free_h)
        low, high = cy - span / 2, cy + span / 2
        pins = list(markers or ())

        name_w, name_h = self.book.font(name_px).size(name)
        name_line = pygame.Rect(0, 0, name_w, name_h)
        name_line.midbottom = (round(cx), round(cy))
        area_line = None
        if area:
            area_w, area_h = self.book.font(area_px, mono=True).size(area)
            area_line = pygame.Rect(0, 0, area_w, area_h)
            area_line.midtop = (round(cx), round(cy + gap))

        shift = self._dodge([name_line] + ([area_line] if area_line else []), pins, low, high)
        if shift is None and area_line is not None:
            # too short to clear the marker with both lines: keep the name and
            # lose the measurement - the name is what identifies the room
            shift = self._dodge([name_line], pins, low, high)
            area = ""
        cy += shift or 0

        blit_text(surface, self.book, name, name_px, self.theme.text, (cx, cy), anchor="midbottom")
        if area:
            blit_text(
                surface, self.book, area, area_px,
                self.theme.inop, (cx, cy + gap), anchor="midtop", mono=True,
            )

    def _device(
        self, surface, device: Device, view: PlanView, vp: Viewport,
        *, level: str, selected: bool, hover: bool, max_u: float = 13.0,
    ) -> None:
        t = self.theme
        colour = t.status_color(level)
        cx, cy = view.to_screen(device.at)
        # Size the marker in *plan* metres, not in viewport ink units: a
        # 15-room house drawn large would otherwise get markers that swamp the
        # rooms they sit in. Clamped so it stays visible when zoomed out and
        # stays tappable at any zoom.
        r = self._marker_radius(view, vp, max_u)
        if selected or hover:
            r *= 1.25
        marker = pygame.Rect(0, 0, round(r * 2), round(r * 2))
        marker.center = (round(cx), round(cy))

        vd.chamfer_rect(surface, marker, fill=t.background, alpha=215, cut=r * 0.5)
        vd.chamfer_rect(surface, marker, outline=colour, width=vp.px(t.stroke), cut=r * 0.5)

        device_icon(surface, device.resolved_kind, (cx, cy), r * 0.55, colour, vp.px(t.stroke))
        if selected:
            vd.bracket_frame(surface, marker.inflate(vp.px(10), vp.px(10)), t.data,
                             width=vp.px(t.stroke), arm=vp.u(7))
        if selected or hover:
            blit_text(
                surface, self.book, device.display_label.upper(), vp.font_px(t.size_micro),
                t.text, (cx, marker.top - vp.u(4)), anchor="midbottom",
            )

    def _scale_bar(self, surface, view: PlanView, vp: Viewport, units: str) -> None:
        t = self.theme
        target_px = view.rect.width * 0.12
        step = 1.0
        while view.length(step) < target_px:
            step *= 2 if step < 4 else 2.5
        while view.length(step) > target_px * 2:
            step /= 2
        length = view.length(step)
        pad = vp.u(t.pad)
        x0 = view.rect.left + pad
        y = view.rect.bottom - pad
        pygame.draw.line(surface, t.inop, (x0, y), (x0 + length, y), vp.px(t.stroke))
        for x in (x0, x0 + length):
            pygame.draw.line(surface, t.inop, (x, y - vp.u(4)), (x, y + vp.u(4)), vp.px(t.stroke))
        blit_text(
            surface, self.book, f"{step:g} {units}", vp.font_px(t.size_micro), t.inop,
            (x0 + length / 2, y - vp.u(6)), anchor="midbottom", mono=True,
        )


def device_at(floor: Floor, view: PlanView, pixel: tuple[float, float], radius_px: float,
              devices: list[Device] | None = None) -> Device | None:
    """Nearest device marker within ``radius_px`` of the pointer.

    ``devices`` narrows the hit test to what is actually on screen - picking a
    marker that is not drawn is worse than picking nothing.
    """
    best: Device | None = None
    best_d = radius_px
    for device in (floor.devices if devices is None else devices):
        dx, dy = view.to_screen(device.at)
        d = math.hypot(dx - pixel[0], dy - pixel[1])
        if d <= best_d:
            best, best_d = device, d
    return best
