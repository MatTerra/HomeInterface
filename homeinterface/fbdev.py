"""Direct-to-panel output for SPI framebuffers, plus raw evdev touch input.

The target hardware is an Adafruit PiTFT 3.5" (HX8357D, 480x320) on a
Raspberry Pi: an *SPI* display driven by the kernel's fbtft driver, which
exposes it as a plain framebuffer device (usually ``/dev/fb1``).  SDL2 has no
fbdev video driver, so pygame cannot open that panel the way it opens a
window - there is nothing to open.  Two pieces bridge the gap:

* :class:`Framebuffer` mmaps the device and pushes a pygame surface into it.
  The surface is created in the panel's own pixel format (RGB565), so a frame
  is a straight copy with no per-pixel conversion, and only the *rows that
  changed* are written.  That second part matters more than it looks: fbtft
  repaints over SPI whatever the mmap dirtied, and a full 480x320x16bpp frame
  is 307kB - roughly 77ms of bus time at 32MHz.  This UI is mostly still, so
  row diffing is the difference between a responsive panel and a slideshow.

* :class:`TouchPanel` reads the touchscreen straight from ``/dev/input/event*``
  and posts pygame mouse events.  The resistive digitiser reports raw ADC
  counts in its own orientation, which has nothing to do with the
  framebuffer's ``rotate=`` setting - hence the swap/invert knobs, which
  ``tools/touchcal.py`` exists to find.

Both are Linux-only and fail loudly: if the panel is not there the caller
should fall back to a window rather than have us paper over it.
"""

from __future__ import annotations

import glob
import mmap
import os
import struct
from dataclasses import dataclass, replace
from typing import Iterator

import pygame

#: pygame channel masks for the RGB565 pixel format fbtft panels use
RGB565_MASKS = (0xF800, 0x07E0, 0x001F, 0x0000)

# linux/fb.h
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
_VAR = struct.Struct("@8I")            # xres..grayscale, enough of fb_var_screeninfo
_FIX = struct.Struct("@16sLIIIIHHHI")  # ..line_length of fb_fix_screeninfo

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0x00
ABS_X, ABS_Y, ABS_PRESSURE = 0x00, 0x01, 0x18
BTN_TOUCH = 0x14A
_EVENT = struct.Struct("@llHHi")       # input_event: timeval, type, code, value
_ABSINFO = struct.Struct("@6i")        # input_absinfo: value, min, max, fuzz, flat, res


class PanelError(RuntimeError):
    """The framebuffer or touch device is missing or unusable."""


def _ioctl(fd: int, request: int, size: int) -> bytes:
    import fcntl

    return fcntl.ioctl(fd, request, b"\0" * size)


def rgb565_surface(size: tuple[int, int]) -> pygame.Surface:
    """A drawing surface in the panel's native pixel format.

    Drawing straight into RGB565 costs a little colour precision (banding is
    visible in large dark gradients) and buys the whole conversion pass: the
    surface's bytes *are* what the panel wants.
    """
    return pygame.Surface(size, 0, 16, RGB565_MASKS)


@dataclass(frozen=True)
class FbGeometry:
    width: int
    height: int
    bits_per_pixel: int
    line_length: int

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def row_bytes(self) -> int:
        return self.width * self.bits_per_pixel // 8


def read_geometry(fd: int) -> FbGeometry:
    """Query a framebuffer device's resolution, depth and stride."""
    width, height, xres_virtual, _yres_v, _xoff, _yoff, bpp, _gray = _VAR.unpack(
        _ioctl(fd, FBIOGET_VSCREENINFO, _VAR.size)
    )
    if not width or not height:
        raise PanelError("framebuffer reports a zero-sized mode")
    line_length = _FIX.unpack(_ioctl(fd, FBIOGET_FSCREENINFO, _FIX.size))[-1]
    # a stride below one visible row means we mis-parsed fb_fix_screeninfo;
    # the virtual width is the reliable fallback
    if line_length < width * bpp // 8:
        line_length = max(width, xres_virtual) * bpp // 8
    return FbGeometry(width, height, bpp, line_length)


class FrameWriter:
    """Copies RGB565 surfaces into a framebuffer-shaped byte target.

    Split out from :class:`Framebuffer` so the row-diffing logic can be tested
    against a plain ``bytearray`` instead of a real panel; ``target`` is
    anything that accepts slice assignment (an ``mmap`` in production).
    """

    def __init__(self, geometry: FbGeometry, target):
        self.geometry = geometry
        self._target = target
        # what the panel is currently showing, so we can push only the delta
        self._shadow = bytearray(geometry.line_length * geometry.height)
        self._first = True

    @property
    def size(self) -> tuple[int, int]:
        return self.geometry.size

    def new_surface(self) -> pygame.Surface:
        return rgb565_surface(self.size)

    def present(self, surface: pygame.Surface) -> int:
        """Push ``surface``; returns the number of bytes actually written.

        Only rows whose bytes differ from the last presented frame are
        written, because fbtft's deferred I/O repaints over SPI exactly what
        the mmap dirtied.  The first frame is always written whole.
        """
        geo = self.geometry
        if surface.get_size() != geo.size:
            raise PanelError(f"surface is {surface.get_size()}, panel is {geo.size}")
        if surface.get_bitsize() != 16:
            raise PanelError(f"surface is {surface.get_bitsize()}bpp, panel wants RGB565")

        pitch = surface.get_pitch()
        row = geo.row_bytes
        written = 0
        buffer = surface.get_buffer()
        try:
            src = memoryview(buffer).cast("B")
            shadow = memoryview(self._shadow)
            for start, stop in self._dirty_runs(src, pitch, row):
                for y in range(start, stop):
                    dst_at = y * geo.line_length
                    src_at = y * pitch
                    chunk = src[src_at:src_at + row]
                    self._target[dst_at:dst_at + row] = chunk
                    shadow[dst_at:dst_at + row] = chunk
                    written += row
        finally:
            del buffer
        self._first = False
        return written

    def _dirty_runs(self, src: memoryview, pitch: int, row: int) -> Iterator[tuple[int, int]]:
        """Yield ``[start, stop)`` runs of rows whose bytes changed."""
        if self._first:
            yield 0, self.geometry.height
            return
        shadow = memoryview(self._shadow)
        line_length = self.geometry.line_length
        run_start: int | None = None
        for y in range(self.geometry.height):
            src_at = y * pitch
            dst_at = y * line_length
            if src[src_at:src_at + row] == shadow[dst_at:dst_at + row]:
                if run_start is not None:
                    yield run_start, y
                    run_start = None
            elif run_start is None:
                run_start = y
        if run_start is not None:
            yield run_start, self.geometry.height

    def blank(self) -> None:
        """Clear the panel, so shutting down does not leave a dead frame up."""
        zeros = b"\0" * len(self._shadow)
        self._target[:len(zeros)] = zeros
        self._shadow[:] = zeros


class Framebuffer(FrameWriter):
    """An mmap'd framebuffer device that takes RGB565 pygame surfaces.

    ``geometry`` is normally probed over ioctl; passing it explicitly is for
    panels whose driver reports a mode we cannot parse.
    """

    def __init__(self, path: str = "/dev/fb1", geometry: FbGeometry | None = None):
        self.path = path
        try:
            self._fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            raise PanelError(
                f"{path}: {exc.strerror} - is the fbtft overlay loaded, and is this "
                f"user in the 'video' group?"
            ) from exc
        try:
            geometry = geometry or read_geometry(self._fd)
            if geometry.bits_per_pixel != 16:
                raise PanelError(
                    f"{path}: {geometry.bits_per_pixel}bpp panel, only 16bpp "
                    f"(RGB565) is supported"
                )
            self._map = mmap.mmap(
                self._fd,
                geometry.line_length * geometry.height,
                mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE,
            )
        except Exception:
            os.close(self._fd)
            raise
        super().__init__(geometry, self._map)

    def close(self) -> None:
        try:
            self._map.close()
        finally:
            os.close(self._fd)


def default_device(preferred: str | None = None) -> str | None:
    """Pick the framebuffer most likely to be the SPI panel.

    fbtft usually lands on ``/dev/fb1`` (``fb0`` being HDMI), but on a board
    booted with no HDMI mode it can be the only framebuffer and take ``fb0``.
    An explicit request is honoured or refused; otherwise fb1 wins over fb0.
    """
    if preferred:
        return preferred if os.path.exists(preferred) else None
    for candidate in ("/dev/fb1", "/dev/fb0"):
        if os.path.exists(candidate):
            return candidate
    return None


# -- touch ---------------------------------------------------------------

def _eviocgabs(axis: int) -> int:
    """_IOR('E', 0x40 + axis, struct input_absinfo)."""
    return (2 << 30) | (_ABSINFO.size << 16) | (ord("E") << 8) | (0x40 + axis)


#: _IOC(_IOC_READ, 'E', 0x06, 256) - EVIOCGNAME(256)
_EVIOCGNAME = (2 << 30) | (256 << 16) | (ord("E") << 8) | 0x06


def device_name(fd: int) -> str:
    try:
        return _ioctl(fd, _EVIOCGNAME, 256).split(b"\0", 1)[0].decode("utf-8", "replace")
    except OSError:
        return ""


def axis_range(fd: int, axis: int) -> tuple[int, int] | None:
    """The ``[min, max]`` an absolute axis reports, or None if it has none."""
    try:
        info = _ABSINFO.unpack(_ioctl(fd, _eviocgabs(axis), _ABSINFO.size))
    except OSError:
        return None
    minimum, maximum = info[1], info[2]
    return (minimum, maximum) if maximum > minimum else None


#: substrings of evdev device names that mean "this is our touchscreen"
TOUCH_HINTS = ("stmpe", "touch", "ft5", "ft6", "ads7846", "goodix", "edt-ft5x06")


def find_touch_device() -> str | None:
    """First ``/dev/input/event*`` that looks like a touchscreen."""
    fallback: str | None = None
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            if axis_range(fd, ABS_X) is None or axis_range(fd, ABS_Y) is None:
                continue
            if any(hint in device_name(fd).lower() for hint in TOUCH_HINTS):
                return path
            # an absolute-pointing device with an unfamiliar name is still a
            # better guess than nothing
            fallback = fallback or path
        finally:
            os.close(fd)
    return fallback


@dataclass(frozen=True)
class TouchCalibration:
    """Maps raw ADC counts to panel pixels.

    Applied in this order: normalise each axis against its reported range,
    swap the two axes if the digitiser is mounted rotated, then invert.  The
    framebuffer's ``rotate=`` parameter does not touch the digitiser, so these
    three flags are what align the two; ``tools/touchcal.py`` finds them.
    """

    x_range: tuple[int, int] = (0, 4095)
    y_range: tuple[int, int] = (0, 4095)
    swap_xy: bool = False
    invert_x: bool = False
    invert_y: bool = False

    def map(self, raw_x: int, raw_y: int, size: tuple[int, int]) -> tuple[int, int]:
        fx = _normalise(raw_x, self.x_range)
        fy = _normalise(raw_y, self.y_range)
        if self.swap_xy:
            fx, fy = fy, fx
        if self.invert_x:
            fx = 1.0 - fx
        if self.invert_y:
            fy = 1.0 - fy
        width, height = size
        return (_clamp(round(fx * (width - 1)), width - 1),
                _clamp(round(fy * (height - 1)), height - 1))

    @classmethod
    def from_dict(cls, data: dict | None) -> TouchCalibration:
        data = data or {}
        cal = cls(
            swap_xy=bool(data.get("swap_xy", False)),
            invert_x=bool(data.get("invert_x", False)),
            invert_y=bool(data.get("invert_y", False)),
        )
        for axis in ("x", "y"):
            span = data.get(f"{axis}_range")
            if isinstance(span, (list, tuple)) and len(span) == 2:
                cal = replace(cal, **{f"{axis}_range": (int(span[0]), int(span[1]))})
        return cal


def _normalise(value: int, span: tuple[int, int]) -> float:
    low, high = span
    if high <= low:
        return 0.0
    return min(1.0, max(0.0, (value - low) / (high - low)))


def _clamp(value: int, high: int) -> int:
    return min(high, max(0, value))


class TouchPanel:
    """Turns raw evdev touch reports into pygame mouse events.

    A touchscreen is not a mouse: it has no hover, and a press arrives with
    its position already known.  Each report ends in ``SYN_REPORT``, at which
    point we emit at most one event - ``MOUSEBUTTONDOWN`` at the new position,
    ``MOUSEMOTION`` while a finger drags, ``MOUSEBUTTONUP`` on release - so
    the existing widget code, which only reads ``event.pos``, works unchanged.
    """

    def __init__(self, path: str, size: tuple[int, int],
                 calibration: TouchCalibration | None = None):
        try:
            self._fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise PanelError(
                f"{path}: {exc.strerror} - is this user in the 'input' group?"
            ) from exc
        self.path = path
        self.size = size
        self.name = device_name(self._fd)
        cal = calibration or TouchCalibration()
        # the driver's own axis ranges beat the defaults, but a calibration
        # that states its ranges explicitly wins over both
        if cal.x_range == TouchCalibration().x_range:
            cal = replace(cal, x_range=axis_range(self._fd, ABS_X) or cal.x_range)
        if cal.y_range == TouchCalibration().y_range:
            cal = replace(cal, y_range=axis_range(self._fd, ABS_Y) or cal.y_range)
        self.calibration = cal
        self._raw = [0, 0]
        self._down = False
        self._pos: tuple[int, int] | None = None
        self._pending_down = False
        self._pending_up = False

    @property
    def pos(self) -> tuple[int, int] | None:
        """Last touched point, or None until the panel is first touched."""
        return self._pos

    @property
    def pressed(self) -> bool:
        return self._down

    def pump(self) -> None:
        """Drain the device and post pygame events. Never blocks."""
        while True:
            try:
                data = os.read(self._fd, _EVENT.size * 64)
            except (BlockingIOError, OSError):
                return
            if not data:
                return
            for offset in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                _sec, _usec, kind, code, value = _EVENT.unpack_from(data, offset)
                self._feed(kind, code, value)

    def _feed(self, kind: int, code: int, value: int) -> None:
        if kind == EV_ABS:
            if code == ABS_X:
                self._raw[0] = value
            elif code == ABS_Y:
                self._raw[1] = value
            elif code == ABS_PRESSURE and not value:
                # some resistive drivers only signal release as zero pressure
                self._pending_up = self._down
        elif kind == EV_KEY and code == BTN_TOUCH:
            if value:
                self._pending_down = True
            else:
                self._pending_up = True
        elif kind == EV_SYN and code == SYN_REPORT:
            self._sync()

    def _sync(self) -> None:
        point = self.calibration.map(self._raw[0], self._raw[1], self.size)
        if self._pending_down and not self._down:
            self._down = True
            self._pos = point
            self._post(pygame.MOUSEBUTTONDOWN, pos=point, button=1, touch=True)
        elif self._down and self._pending_up:
            self._post(pygame.MOUSEBUTTONUP, pos=self._pos or point, button=1, touch=True)
            self._down = False
        elif self._down and point != self._pos:
            previous = self._pos or point
            self._pos = point
            self._post(pygame.MOUSEMOTION, pos=point, buttons=(1, 0, 0), touch=True,
                       rel=(point[0] - previous[0], point[1] - previous[1]))
        self._pending_down = False
        self._pending_up = False

    @staticmethod
    def _post(kind: int, **attrs: object) -> None:
        pygame.event.post(pygame.event.Event(kind, **attrs))

    def close(self) -> None:
        os.close(self._fd)
