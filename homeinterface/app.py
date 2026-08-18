"""Application shell: window, chrome, screen stack, main loop.

The shell owns the persistent furniture - title bar, nav rail, message strip,
status bar - and hands the remaining rectangle to the active screen.  Every
piece of that furniture is sized in design units, so the whole chrome scales
with the panel instead of shrinking away on a 4K display.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Any

import pygame

from . import draw as vd
from . import fbdev
from .backend import Backend, Link, build_backend
from .config import load_config, resolve_path
from .dashboard import DashboardError, DashboardScreen, load_dashboard
from .floorplan import FloorPlan, PlanError, load_plan
from .fonts import FontBook, blit_text
from .scaling import Box, Viewport
from .screens import OverviewScreen, PlanScreen, Screen, SystemsScreen
from .screens.alt import AltHomeScreen, AltVitalsScreen
from .theme import Theme, mix
from .ui.base import UIContext
from .ui.controls import Button
from .ui.indicators import Clock, MessageStrip

SCREEN_TYPES = [PlanScreen, OverviewScreen, SystemsScreen]
#: the alternative shell (``--alternative``): drill-down navigation instead of
#: the scale drawing, and a bottom tab bar instead of the side rail. Same
#: screen keys, so ``start_screen`` and the number shortcuts still work.
ALT_SCREEN_TYPES = [AltHomeScreen, AltVitalsScreen, SystemsScreen]
#: the shells a config may ask for; ``custom`` reads its screens from a file
SHELLS = ("stock", "alt", "custom")

# Design-unit sizes of the persistent chrome - i.e. pixels on the 480x320
# reference panel.  The rail is sized so its buttons clear the theme's touch
# minimum; the footer only appears when there is something to report, because
# 320px of height cannot spare a permanently empty strip.
TITLE_H = 30.0
RAIL_W = 56.0
FOOTER_H = 26.0
MIN_TITLE_PX = 26
MIN_RAIL_PX = 44
MIN_FOOTER_PX = 20

# Alternative shell: the nav lives in a bottom bar, and the title bar gives up
# its second line - on a 320px panel the rail costs 12% of the width and the
# tall title another 9% of the height, both of which the content wants back.
ALT_TITLE_H = 24.0
ALT_TABS_H = 46.0
MIN_ALT_TABS_PX = 40


class App:
    def __init__(self, config: dict[str, Any] | None = None, config_path: str | Path | None = None):
        self.config = config if config is not None else load_config(config_path)
        self.theme = Theme.from_dict(self.config.get("theme"))
        self.plan = self._load_plan()
        self.backend: Backend = build_backend(
            self.config,
            entity_ids=self.plan.entity_ids,
            labels=self.plan.labels,
            plan=self.plan,
        )
        self.running = False
        self.screen_index = 0
        self.screens: list[Screen] = []
        self._fullscreen = bool(self.config["display"].get("fullscreen", False))
        #: which whole set of screens runs: stock | alt | custom
        self.shell = self._shell()
        self.dashboard = self._load_dashboard() if self.shell == "custom" else None
        self._show_fps = False
        self.surface: pygame.Surface | None = None
        # set only when we are painting an SPI panel instead of a window
        self.fb: fbdev.Framebuffer | None = None
        self.touch: fbdev.TouchPanel | None = None
        self.vp = Viewport(1, 1)
        self.book: FontBook | None = None
        self._redraw_requested = True
        self._last_revision: int | None = None
        self._last_second: int | None = None
        self._last_blink: bool | None = None

    # -- setup -----------------------------------------------------------
    def _shell(self) -> str:
        ui = self.config.get("ui") or {}
        shell = str(ui.get("shell", "stock")).lower()
        if shell not in SHELLS:
            raise ValueError(f"ui.shell must be {' | '.join(SHELLS)}, got {shell!r}")
        return shell

    @property
    def alternative(self) -> bool:
        """True for the drill-down shell; kept because the chrome differs."""
        return self.shell == "alt"

    def _load_dashboard(self):
        path = resolve_path(self.config, self.config.get("dashboard"))
        if path is None:
            raise DashboardError("ui.shell is custom but no dashboard: is configured")
        return load_dashboard(path)

    def reload_dashboard(self) -> bool:
        """Re-read the dashboard file, keeping the old one if it will not load.

        The panel has no keyboard and this is how a dashboard is edited in
        practice (SIGHUP over ssh), so a typo must never leave a black screen:
        the running tree stays up and the error goes to the message strip.
        """
        if self.shell != "custom":
            return False
        try:
            dashboard = self._load_dashboard()
        except DashboardError as exc:
            print(f"[dashboard] {exc}")
            self.backend.raise_alert("dashboard.reload", f"DASHBOARD {exc}", "caution")
            return False
        self.dashboard = dashboard
        self.backend.clear_alert("dashboard.reload")
        self._build_screens()
        self.request_redraw()
        return True

    def _load_plan(self) -> FloorPlan:
        path = resolve_path(self.config, self.config.get("plan"))
        if path is None or not Path(path).exists():
            return FloorPlan(name="No plan", floors=[])
        try:
            return load_plan(path)
        except PlanError as exc:
            print(f"[plan] {exc}")
            return FloorPlan(name="Plan error", floors=[])

    def _open_output(self) -> None:
        """Open either an SPI panel or a desktop window, per ``display.driver``.

        ``auto`` prefers the panel when there is one and no desktop session to
        put a window in, so the same command works over ssh on the Pi and on a
        development machine.
        """
        driver = str(self.config["display"].get("driver", "auto")).lower()
        if driver not in ("auto", "window", "fbdev"):
            raise ValueError(f"display.driver must be auto|window|fbdev, got {driver!r}")
        if driver == "fbdev" or (driver == "auto" and self._panel_likely()):
            try:
                self._open_panel()
                return
            except fbdev.PanelError as exc:
                if driver == "fbdev":
                    raise
                print(f"[panel] {exc}\n[panel] falling back to a window")
        self._open_window()

    def _panel_likely(self) -> bool:
        """True when a framebuffer panel is the only plausible output."""
        if os.name != "posix":
            return False
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return False
        return fbdev.default_device(self.config["display"].get("fbdev")) is not None

    def _open_panel(self) -> None:
        display = self.config["display"]
        device = fbdev.default_device(display.get("fbdev"))
        if device is None:
            raise fbdev.PanelError(
                f"no framebuffer device (looked for {display.get('fbdev') or '/dev/fb1, /dev/fb0'})"
            )
        # SDL never sees the panel: it exists only to give us an event queue
        # and a font/timer subsystem, so point it at a driver that needs no
        # display server at all.
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        self.fb = fbdev.Framebuffer(device)
        pygame.display.set_mode(self.fb.size)
        self.surface = self.fb.new_surface()
        self._open_touch(self.fb.size)
        # there is no window to close and no keyboard on a panel, so the only
        # way out is a signal - make it a clean one
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: setattr(self, "running", False))
        print(f"[panel] {device} {self.fb.size[0]}x{self.fb.size[1]} RGB565"
              f"{f', touch {self.touch.path} ({self.touch.name})' if self.touch else ', no touch'}")
        self._on_resize(self.surface.get_size())

    def _open_touch(self, size: tuple[int, int]) -> None:
        wanted = str(self.config["display"].get("touch", "auto")).lower()
        if wanted == "none":
            return
        path = fbdev.find_touch_device() if wanted == "auto" else self.config["display"]["touch"]
        if not path:
            print("[panel] no touch device found (display.touch: auto)")
            return
        calibration = fbdev.TouchCalibration.from_dict(self.config["display"].get("touch_calibration"))
        try:
            self.touch = fbdev.TouchPanel(str(path), size, calibration)
        except fbdev.PanelError as exc:
            # a panel with no touch is still a usable dashboard
            print(f"[panel] touch disabled: {exc}")

    def _open_window(self) -> None:
        display = self.config["display"]
        pygame.init()
        pygame.display.set_caption(str(display.get("title", "HOME INTERFACE")))
        flags = pygame.RESIZABLE if display.get("resizable", True) else 0
        size = (int(display.get("width", 1600)), int(display.get("height", 900)))
        if self._fullscreen:
            flags |= pygame.FULLSCREEN
            size = (0, 0)
        try:
            self.surface = pygame.display.set_mode(size, flags, vsync=1 if display.get("vsync", True) else 0)
        except pygame.error:
            self.surface = pygame.display.set_mode(size, flags)
        pygame.mouse.set_visible(bool(display.get("cursor", True)))
        self._on_resize(self.surface.get_size())

    def _on_resize(self, size: tuple[int, int]) -> None:
        density = float(self.config["display"].get("density", 1.0))
        self.vp = Viewport(max(1, size[0]), max(1, size[1]), density=density)
        if self.book is None:
            self.book = FontBook(self.theme)
        else:
            self.book.clear_raster_cache()
        vd.clear_caches()
        for screen in self.screens:
            screen.invalidate()
            renderer = getattr(screen, "renderer", None)
            if renderer is not None:
                renderer.invalidate()
        self.request_redraw()

    def _build_screens(self) -> None:
        self.clock_widget = Clock()
        self.footer_messages = MessageStrip(max_lines=3)
        if self.shell == "custom":
            # one tree, no screen list: the dashboard owns its own navigation
            # (docs/adr/0003), so the shell has no rail and no tab bar to draw
            assert self.dashboard is not None
            self.screens = [DashboardScreen(self, self.dashboard)]
            self.screen_index = 0
            self.nav = []
            return
        types = ALT_SCREEN_TYPES if self.alternative else SCREEN_TYPES
        self.screens = [cls(self) for cls in types]
        start = str(self.config.get("start_screen", "plan"))
        self.screen_index = next((i for i, s in enumerate(self.screens) if s.key == start), 0)
        self.nav = [
            # the sub-label doubles as the keyboard shortcut hint
            Button(screen.title, (lambda i=i: self.show(i)), compact=True, sub=str(i + 1))
            for i, screen in enumerate(self.screens)
        ]

    @property
    def screen(self) -> Screen:
        return self.screens[self.screen_index]

    def show(self, index: int) -> None:
        self.request_redraw()
        if index == self.screen_index or not (0 <= index < len(self.screens)):
            return
        ctx = self._context()
        self.screen.on_exit(ctx)
        self.screen_index = index
        self.screen.on_enter(ctx)

    def request_redraw(self) -> None:
        """Force the next tick in :meth:`run` to draw and flip."""
        self._redraw_requested = True

    # -- context ---------------------------------------------------------
    def _context(self) -> UIContext:
        assert self.book is not None
        # a touch panel has no cursor: the pointer is wherever the last finger
        # was, and only while it is down
        pointer = self.touch.pos or (0, 0) if self.touch else pygame.mouse.get_pos()
        pressed = self.touch.pressed if self.touch else any(pygame.mouse.get_pressed())
        return UIContext(
            theme=self.theme,
            book=self.book,
            vp=self.vp,
            backend=self.backend,
            now=time.monotonic(),
            pointer=pointer,
            pressed=pressed,
        )

    # -- chrome geometry -------------------------------------------------
    def _regions(self, ctx: UIContext) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect]:
        gap = ctx.u(self.theme.gap)
        pad = ctx.u(self.theme.pad)
        title_h = max(ctx.u(ALT_TITLE_H if self.alternative else TITLE_H), MIN_TITLE_PX)
        rail_w = max(ctx.u(RAIL_W), MIN_RAIL_PX)
        full = Box(self.vp.rect).pad(pad)
        title, rest = full.rows(title_h, full.rect.height - title_h - gap, gap=gap)

        # The message strip is claimed only when there is a message. On a
        # 320px-tall panel a permanently empty strip is 8% of the screen.
        if self.backend.alerts():
            footer_h = max(ctx.u(FOOTER_H), MIN_FOOTER_PX)
            body, footer = rest.rows(rest.rect.height - footer_h - gap, footer_h, gap=gap)
            footer_rect = footer.rect
        else:
            body = rest
            footer_rect = pygame.Rect(rest.rect.left, rest.rect.bottom, rest.rect.width, 0)

        if self.shell == "custom":
            # the whole body is the dashboard's; its tabs are a node in the
            # tree, not chrome the shell draws around it
            empty = pygame.Rect(body.rect.left, body.rect.top, 0, 0)
            return title.rect, empty, body.rect, footer_rect

        if self.alternative:
            # the nav bar is a row along the bottom, under everything else
            tabs_h = max(ctx.u(ALT_TABS_H), MIN_ALT_TABS_PX)
            content, tabs = body.rows(body.rect.height - tabs_h - gap, tabs_h, gap=gap)
            return title.rect, tabs.rect, content.rect, footer_rect

        rail, content = body.cols(rail_w, body.rect.width - rail_w - gap, gap=gap)
        return title.rect, rail.rect, content.rect, footer_rect

    # -- loop ------------------------------------------------------------
    def run(self) -> int:
        self._open_output()
        self._install_reload_signal()
        self._build_screens()
        self.backend.start()
        clock = pygame.time.Clock()
        fps = int(self.config["display"].get("fps", 60))
        self.running = True
        try:
            while self.running:
                self.tick()
                clock.tick(fps)
                self._fps = clock.get_fps()
        finally:
            self.backend.stop()
            self._close_output()
            pygame.quit()
        return 0

    def _install_reload_signal(self) -> None:
        """SIGHUP re-reads the dashboard: the panel has no keyboard to press F5 on."""
        if self.shell != "custom" or not hasattr(signal, "SIGHUP"):
            return
        signal.signal(signal.SIGHUP, lambda *_: self.reload_dashboard())

    def tick(self) -> None:
        """One frame: lay out, drain input, draw and present if anything moved.

        Split out of :meth:`run` so a test can drive real frames — including
        the event that switches screens — without a clock or a window.
        """
        ctx = self._context()
        title_r, rail_r, content_r, footer_r = self._regions(ctx)
        self._layout_chrome(rail_r, title_r, footer_r, ctx)
        # laid out before the events so widget hit-testing has real rects
        self.screen.ensure_layout(content_r, ctx)

        if self.touch is not None:
            # feeds synthetic mouse events into the queue we read below
            self.touch.pump()
        events = pygame.event.get()
        for event in events:
            self._handle(event, ctx)

        second = int(ctx.now)
        blink = ctx.blink
        revision = self.backend.revision
        dirty = (
            self._redraw_requested
            or bool(events)
            or revision != self._last_revision
            or second != self._last_second
            or blink != self._last_blink
        )
        self._last_revision = revision
        self._last_second = second
        self._last_blink = blink

        if not dirty:
            return
        self._redraw_requested = False
        # an event may have switched screens since the call above; the
        # incoming screen has never been laid out, and drawing a screen whose
        # layout() never ran raises AttributeError. No-op when the rect is
        # unchanged and the layout already ran.
        self.screen.ensure_layout(content_r, ctx)
        self._draw(ctx, title_r, rail_r, content_r, footer_r)
        self._present()

    def _present(self) -> None:
        """Show the frame we just drew: SPI push on a panel, flip in a window."""
        if self.fb is not None:
            assert self.surface is not None
            self.fb.present(self.surface)
        else:
            pygame.display.flip()

    def _close_output(self) -> None:
        if self.touch is not None:
            self.touch.close()
            self.touch = None
        if self.fb is not None:
            # leaving the last frame lit on a panel with no process behind it
            # reads as "running but frozen"
            self.fb.blank()
            self.fb.close()
            self.fb = None

    def _layout_chrome(self, rail: pygame.Rect, title: pygame.Rect, footer: pygame.Rect, ctx: UIContext) -> None:
        gap = ctx.u(self.theme.gap)
        if not self.nav:
            self.clock_widget.layout(title)
            if footer.height > 0:
                self.footer_messages.layout(Box(footer).pad(ctx.u(self.theme.pad), ctx.u(4)).rect)
            return
        if self.alternative:
            # one wide tab per screen, filling the bar: the whole bottom edge
            # is nav, which is the easiest thing on a panel to hit blind
            for button, cell in zip(self.nav, Box(rail).cols(*[1.0] * len(self.nav), gap=gap)):
                button.layout(cell.rect)
            self.clock_widget.layout(title)
            if footer.height > 0:
                self.footer_messages.layout(
                    Box(footer).pad(ctx.u(self.theme.pad), ctx.u(4)).rect
                )
            return
        cells = Box(rail).rows(*[1.0] * len(self.nav), gap=gap)
        # nav buttons are square-ish and top-aligned; the rail's tail stays empty
        button_h = min(ctx.u(96), cells[0].rect.height)
        for i, button in enumerate(self.nav):
            button.layout(pygame.Rect(rail.left, round(rail.top + i * (button_h + gap)),
                                      rail.width, round(button_h)))
        self.clock_widget.layout(title)
        if footer.height > 0:
            self.footer_messages.layout(
                Box(footer).pad(ctx.u(self.theme.pad), ctx.u(4)).rect
            )

    def _handle(self, event: pygame.event.Event, ctx: UIContext) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type == pygame.VIDEORESIZE:
            self._on_resize((event.w, event.h))
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.running = False
                return
            if event.key == pygame.K_F11:
                self._toggle_fullscreen()
                return
            if event.key == pygame.K_F12:
                self._screenshot()
                return
            if event.key == pygame.K_F3:
                self._show_fps = not self._show_fps
                return
            if event.key == pygame.K_F5:
                self.reload_dashboard()
                return
            if event.key == pygame.K_TAB:
                if isinstance(self.screen, DashboardScreen):
                    self.screen.cycle()
                else:
                    self.show((self.screen_index + 1) % len(self.screens))
                return
            if pygame.K_1 <= event.key <= pygame.K_9:
                if isinstance(self.screen, DashboardScreen):
                    self.screen.cycle_to(event.key - pygame.K_1)
                else:
                    self.show(event.key - pygame.K_1)
                return
        for button in self.nav:
            if button.handle(event, ctx):
                return
        self.screen.handle(event, ctx)

    def _toggle_fullscreen(self) -> None:
        if self.fb is not None:
            return  # a panel is already the whole screen
        self._fullscreen = not self._fullscreen
        display = self.config["display"]
        if self._fullscreen:
            self.surface = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.surface = pygame.display.set_mode(
                (int(display.get("width", 1600)), int(display.get("height", 900))), pygame.RESIZABLE
            )
        self._on_resize(self.surface.get_size())

    def _screenshot(self) -> None:
        if self.surface is None:
            return
        target = Path("screenshots")
        target.mkdir(exist_ok=True)
        name = target / f"home_{time.strftime('%Y%m%d_%H%M%S')}.png"
        pygame.image.save(self.surface, str(name))
        self.backend.raise_alert("ui.shot", f"SCREENSHOT {name.name}", "info")

    # -- drawing ---------------------------------------------------------
    def _draw(self, ctx: UIContext, title: pygame.Rect, rail: pygame.Rect,
              content: pygame.Rect, footer: pygame.Rect) -> None:
        assert self.surface is not None
        t = self.theme
        self.surface.fill(t.background)
        vd.hairline_grid(self.surface, self.vp.rect, t.rule, spacing=ctx.u(40), alpha=14)

        self._draw_title(self.surface, title, ctx)
        for i, button in enumerate(self.nav):
            button.active = i == self.screen_index
            button.draw(self.surface, ctx)
        self.screen.draw(self.surface, ctx)
        self._draw_footer(self.surface, footer, ctx)

    def _draw_title(self, surface: pygame.Surface, rect: pygame.Rect, ctx: UIContext) -> None:
        t = self.theme
        cut = ctx.u(t.chamfer)
        vd.chamfer_rect(surface, rect, fill=t.panel, cut=cut)
        vd.chamfer_rect(surface, rect, outline=t.rule, width=ctx.px(t.stroke), cut=cut)

        compact = ctx.vp.compact
        # the subtitle needs a second text line's worth of bar to sit in
        name_size = ctx.font_px(t.size_body if compact else t.size_large)
        sub_size = ctx.font_px(t.size_micro)
        show_sub = rect.height >= name_size + sub_size + ctx.u(8)

        pad = ctx.u(t.pad) if not compact else ctx.u(4)
        name_y = rect.centery - sub_size * 0.6 if show_sub else rect.centery
        name = blit_text(surface, ctx.book, self.plan.name.upper(), name_size, t.text,
                         (rect.left + pad, name_y), anchor="midleft")
        if show_sub:
            blit_text(surface, ctx.book, self.screen.subtitle, sub_size, t.inop,
                      (rect.left + pad, rect.centery + name_size * 0.55), anchor="midleft", mono=True)

        # the lamp always fits; its caption is the first thing to go
        link = self.backend.link
        colour = t.status_color(link.level)
        side = max(ctx.u(14), 8)
        lamp = pygame.Rect(0, 0, round(side), round(side))
        lit = link is not Link.OFFLINE or ctx.blink
        # nominally at a fixed offset from centre, but never on top of the
        # house name - a long name wins and pushes the lamp right
        lamp_x = max(rect.centerx - ctx.u(90) - side / 2, name.right + ctx.u(8))
        lamp.midleft = (round(lamp_x), rect.centery)
        vd.chamfer_rect(surface, lamp, fill=colour if lit else mix(t.panel, colour, 0.15), cut=side * 0.35)
        if not compact:
            blit_text(surface, ctx.book, f"LINK {link.value.upper()}", ctx.font_px(t.size_small), colour,
                      (lamp.right + ctx.u(9), rect.centery), anchor="midleft", mono=True)

        self.clock_widget.rect = pygame.Rect(rect)
        # the date line needs the same second row the subtitle does
        self.clock_widget.compact = compact or not show_sub
        self.clock_widget.draw(surface, ctx)

        if self._show_fps:
            blit_text(surface, ctx.book, f"{getattr(self, '_fps', 0.0):5.1f} FPS  {self.vp.width}x{self.vp.height}",
                      ctx.font_px(t.size_micro), t.inop,
                      (rect.centerx + ctx.u(120), rect.centery), anchor="midleft", mono=True)

    def _draw_footer(self, surface: pygame.Surface, rect: pygame.Rect, ctx: UIContext) -> None:
        if rect.height <= 0:
            return
        t = self.theme
        cut = ctx.u(t.chamfer)
        alerts = self.backend.alerts()
        level = alerts[0].level if alerts else "normal"
        vd.chamfer_rect(surface, rect, fill=t.panel, cut=cut)
        vd.chamfer_rect(surface, rect, outline=t.status_color(level) if alerts else t.rule,
                        width=ctx.px(t.stroke), cut=cut)
        self.footer_messages.max_lines = max(1, int(rect.height / (ctx.font_px(t.size_small) * 1.35)))
        self.footer_messages.lines = [(a.level, a.text) for a in alerts]
        self.footer_messages.draw(surface, ctx)

        # keyboard hints are noise on a touch panel, and there is no room
        if not ctx.vp.compact and ctx.vp.width >= ctx.u(700):
            blit_text(surface, ctx.book, "ESC QUIT · TAB NEXT · F11 FULLSCREEN · F12 SHOT · F3 FPS",
                      ctx.font_px(t.size_micro), mix(t.inop, t.background, 0.3),
                      (rect.right - ctx.u(t.pad), rect.bottom - ctx.u(4)), anchor="bottomright", mono=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Home control interface")
    parser.add_argument("-c", "--config", default="config/app.yaml", help="path to app.yaml")
    parser.add_argument("--plan", help="override the floor plan file")
    parser.add_argument("--backend", choices=["mock", "homeassistant"], help="override backend kind")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--alternative", action="store_true",
                        help="alternative small-screen shell: drill-down lists, bottom tab bar")
    parser.add_argument("--custom", nargs="?", const=True, metavar="DASHBOARD",
                        help="custom shell: the screens declared in a dashboard file")
    parser.add_argument("--density", type=float, help="UI scale multiplier (touch panels: 1.2-1.5)")
    parser.add_argument("--driver", choices=["auto", "window", "fbdev"],
                        help="output path: SDL window, or mmap'd SPI framebuffer")
    parser.add_argument("--fbdev", help="framebuffer device (default: probe /dev/fb1, /dev/fb0)")
    parser.add_argument("--touch", help="touch device: auto | none | /dev/input/eventN")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.plan:
        config["plan"] = args.plan
    if args.backend:
        config["backend"] = {**config.get("backend", {}), "kind": args.backend}
    for key in ("width", "height", "density", "driver", "fbdev", "touch"):
        value = getattr(args, key)
        if value is not None:
            config["display"][key] = value
    if args.fullscreen:
        config["display"]["fullscreen"] = True
    if args.alternative:
        config["ui"] = {**(config.get("ui") or {}), "shell": "alt"}
    if args.custom:
        config["ui"] = {**(config.get("ui") or {}), "shell": "custom"}
        if isinstance(args.custom, str):
            config["dashboard"] = args.custom

    try:
        return App(config).run()
    except DashboardError as exc:
        # a dashboard that cannot be trusted is not started: a panel showing
        # half a screen is worse than one that says why it did not come up
        print(f"[dashboard] {exc}")
        return 2
