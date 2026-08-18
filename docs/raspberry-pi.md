# Raspberry Pi 3 B + Adafruit PiTFT 3.5" (480x320)

End-to-end setup for the target hardware: a Pi 3 Model B v1.2 running the
interface headless on an Adafruit PiTFT 3.5" resistive touchscreen (HX8357D
display + STMPE610 digitiser, both on SPI).

The panel is **not** an HDMI display. The kernel's `fbtft` driver exposes it as
a framebuffer device (`/dev/fb1`), and SDL2 has no fbdev video driver — so
there is nothing for pygame to open. `homeinterface/fbdev.py` mmaps the device
and pushes RGB565 rows into it directly, and reads the touchscreen straight
from `/dev/input/event*`. No X, no Wayland, no `fbcp` mirroring daemon.

Why that route: 480x320x16bpp is 307kB per frame, about 77ms of bus time at
32MHz, so a full repaint caps out near 13fps no matter which tool pushes it.
This UI is mostly still, so the app writes **only the rows that changed** — the
chrome, the floor plan and the untouched gauges cost nothing per frame.

---

## 1. Flash the OS

Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)** (Bookworm).

- *Lite*: nothing draws to the panel except us, so a desktop is wasted RAM on a
  1GB board.
- *64-bit*: `pygame-ce` ships prebuilt aarch64 wheels. On 32-bit (`armhf`)
  there is no wheel and `pip` compiles pygame and SDL from source on a Pi 3 —
  tens of minutes, plus a pile of `-dev` packages.

Before writing, open the gear / `Ctrl+Shift+X` (OS customisation) and set:

- hostname, e.g. `homeinterface`
- username + password (used in the systemd unit below)
- Wi-Fi SSID/password + country
- **Enable SSH** (password or your public key)

That is the whole headless setup — no keyboard or HDMI needed at any point.

## 2. Mount the HAT

Power off, seat the PiTFT on all 40 pins. It uses SPI0: `CE0` for the display,
`CE1` for the STMPE610 touch controller, plus GPIO 24/25 for DC/reset and GPIO
18 for the backlight. Nothing else may claim those pins.

## 3. Enable the panel

Edit `/boot/firmware/config.txt` (Bookworm's path — on older images it is
`/boot/config.txt`):

```ini
dtparam=spi=on
dtoverlay=pitft35-resistive,rotate=90,speed=32000000,fps=30

# headless board: the GPU never composites anything, give the RAM to Linux
gpu_mem=16
```

- `rotate=90` gives the landscape 480x320 the UI is designed around. It rotates
  the *framebuffer* only — the digitiser keeps reporting in its own
  orientation, which is what step 7 sorts out.
- `speed` is the SPI clock. 32MHz is the safe starting point; see
  [tuning](#tuning) before raising it.
- `fps` caps how often fbtft's deferred I/O flushes dirty pages to the panel.

Optionally put the boot console on the TFT so you can see kernel messages while
bringing it up — append `fbcon=map:1` to `/boot/firmware/cmdline.txt` (one
line, space-separated). Remove it once the app runs; a console cursor blinking
under our frames is just noise.

Then grant the app access to the framebuffer and input devices, and reboot:

```bash
sudo usermod -aG video,input,spi,gpio $USER
sudo reboot
```

## 4. Verify the panel before touching Python

```bash
ls /dev/fb*                          # expect fb1 (fb0 is HDMI)
dmesg | grep -iE 'hx8357|stmpe|fb1'  # driver probe + which fb it took
sudo apt install -y fbset && fbset -fb /dev/fb1
cat /proc/bus/input/devices          # find the stmpe-ts event device
```

`fbset` must report `geometry 480 320 480 320 16`. If it says `320 480`, the
`rotate=90` did not take.

A destructive-looking but harmless smoke test — it fills the panel with noise,
and the next frame the app draws overwrites it:

```bash
cat /dev/urandom > /dev/fb1
```

If the panel stays white, the display half never probed: check `dmesg`, the
overlay name, and that the HAT is fully seated. If `/dev/fb1` does not exist at
all but `/dev/fb0` does and HDMI is unplugged, fbtft may have taken `fb0` —
pass `--fbdev /dev/fb0`.

## 5. Install the app

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv

git clone <your-repo-url> ~/HomeInterface
cd ~/HomeInterface
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 6. First run

```bash
.venv/bin/python main.py --driver fbdev
```

It prints what it opened, e.g.:

```
[panel] /dev/fb1 480x320 RGB565, touch /dev/input/event0 (stmpe-ts)
```

`--driver auto` (the default) picks the panel whenever a framebuffer exists and
no desktop session does, so the plain `python main.py` works over ssh on the Pi
*and* still opens a window on a development machine. `--driver fbdev` forces
it and fails loudly instead of falling back — use it in the service unit.

There is no keyboard on a panel, so `ESC` cannot quit and `F3`/`F11`/`F12` are
unreachable. `Ctrl-C` over ssh, or `SIGTERM` from systemd, shuts down cleanly
and blanks the panel.

## 7. Calibrate the touchscreen

The digitiser's orientation is independent of `rotate=`, and a resistive
panel's corners never reach the driver's nominal `0..4095`. Tap four targets:

```bash
.venv/bin/python tools/touchcal.py
```

It draws a crosshair in each corner of the panel, works out which raw axis
follows which screen axis, extrapolates the real axis ranges, and prints a
block to paste into `config/app.yaml`:

```yaml
display:
  touch_calibration:
    swap_xy: true
    invert_x: false
    invert_y: true
    x_range: [193, 3902]
    y_range: [221, 3811]
```

`tools/touchcal.py --raw` just dumps raw counts and the mapped pixel, which is
the faster way to check an existing calibration.

## 8. Autostart

The unit lives in the repo at [`deploy/homeinterface.service`](../deploy/homeinterface.service).
It pins the floor plan on the command line rather than in `config/app.yaml`:
the committed config keeps pointing at the example plan so a fresh clone runs
anywhere, and the panel — the one machine that must always show the real house
— states its plan in the unit that starts it.

```bash
sudo cp deploy/homeinterface.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homeinterface
journalctl -u homeinterface -f
```

Replace `mateus` with your username in `User=`, `WorkingDirectory=` and
`ExecStart=` if it differs. `--driver fbdev` is deliberate: it fails loudly
instead of silently falling back to a window that nobody can see.

The touchscreen module is not autoloaded on a headless Pi — the STMPE platform
device is created by the overlay, but nothing binds a driver to it, so
`/dev/input/` stays empty and the app reports `no touch device found`. Load it
at boot:

```bash
echo stmpe_ts | sudo tee /etc/modules-load.d/stmpe.conf
```

## Tuning

| Knob | Where | Effect |
|---|---|---|
| `speed=` | `config.txt` overlay | SPI clock. 32MHz is safe; 42–62MHz is usually fine on the short HAT traces but shows up as sparkle or torn rows when it is not. Raise it one step at a time. |
| `fps=` | `config.txt` overlay | Upper bound on fbtft's flush rate. Above the app's own `display.fps` it does nothing. |
| `display.fps` | `config/app.yaml` | Frame budget of the loop (default 30). The loop already skips drawing entirely when nothing changed. |
| `display.density` | `config/app.yaml` | 1.0 means design units are pixels — the reference size the UI was laid out for. Raise it only if you find touch targets too small, and re-check that nothing clips. |

Two things keep this fast, and both are worth knowing before you go chasing
frame rates: the loop does not redraw unless an event, a backend revision, the
clock second or the blink phase changed, and `FrameWriter.present()` writes
only changed rows. A static screen therefore costs no SPI traffic at all.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Permission denied` on `/dev/fb1` | user not in `video` group, or you did not log out/in after `usermod`. |
| `Permission denied` on `/dev/input/eventN` | same, for the `input` group. The app keeps running without touch and says so. |
| `no touch device found` while `dmesg` shows `stmpe610 detected` | the `stmpe_ts` module is not loaded, so the platform device never becomes an input device. `sudo modprobe stmpe_ts`, then persist it in `/etc/modules-load.d/stmpe.conf`. |
| `no framebuffer device` | overlay not loaded — check `dmesg` and `/boot/firmware/config.txt`. |
| `Nbpp panel, only 16bpp is supported` | you pointed it at HDMI's `fb0` (usually 32bpp) instead of the TFT. |
| Taps land mirrored or on the wrong axis | run `tools/touchcal.py`. |
| Panel readable, but rotated 90° | fix `rotate=` in the overlay, not in the app. |
| Slow, laggy repaints on the plan screen | panning/zooming dirties every row; lower `display.fps` or raise SPI `speed=`. |
| Blank panel after the service stops | expected: shutdown blanks it so a frozen frame cannot be mistaken for a running app. |
