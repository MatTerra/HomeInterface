from __future__ import annotations

import time

import pytest

from homeinterface.backend import Backend, Entity, MockBackend, build_backend
from homeinterface.backend.homeassistant import websocket_url


# -- MockBackend ----------------------------------------------------------


@pytest.fixture
def backend():
    b = MockBackend(["light.sala", "cover.portao", "binary_sensor.smoke", "binary_sensor.door"],
                     chaos=False)
    yield b
    b.stop()


def test_start_stop(backend):
    backend.start()
    assert backend.link.value == "online"
    backend.stop()
    assert backend.link.value == "offline"
    # stop() must be idempotent
    backend.stop()


def test_toggle_flips_light_and_bumps_revision(backend):
    entity = backend.get("light.sala")
    assert entity is not None
    assert entity.state == "off"
    rev_before = backend.revision

    backend.toggle("light.sala")

    entity = backend.get("light.sala")
    assert entity.state == "on"
    assert backend.revision > rev_before

    backend.toggle("light.sala")
    assert backend.get("light.sala").state == "off"


def test_set_brightness_zero_turns_off(backend):
    backend.toggle("light.sala")
    backend.set_brightness("light.sala", 0)
    entity = backend.get("light.sala")
    assert entity.state == "off"


def test_set_brightness_50_sets_brightness_and_on(backend):
    backend.set_brightness("light.sala", 50)
    entity = backend.get("light.sala")
    assert entity.state == "on"
    assert entity.attributes["brightness"] == pytest.approx(127.5, abs=1)


def test_cover_position(backend):
    backend.set_cover_position("cover.portao", 40)
    entity = backend.get("cover.portao")
    assert entity.attributes["current_position"] == 40
    assert entity.state == "open"

    backend.set_cover_position("cover.portao", 0)
    entity = backend.get("cover.portao")
    assert entity.state == "closed"


def test_by_domain_filtering(backend):
    lights = backend.by_domain("light")
    assert all(e.domain == "light" for e in lights)
    assert {"light.sala"} <= {e.entity_id for e in lights}

    mixed = backend.by_domain("light", "cover")
    assert {e.domain for e in mixed} <= {"light", "cover"}


# -- Entity.level -----------------------------------------------------------


def test_entity_level_unavailable():
    entity = Entity("light.x", "unavailable")
    assert entity.level == "inop"


def test_entity_level_smoke_on_is_warning():
    entity = Entity("binary_sensor.smoke", "on", {"device_class": "smoke"})
    assert entity.level == "warning"


def test_entity_level_door_on_is_caution():
    entity = Entity("binary_sensor.door", "on", {"device_class": "door"})
    assert entity.level == "caution"


def test_entity_level_generic_on_off():
    assert Entity("light.x", "on").level == "on"
    assert Entity("light.x", "off").level == "off"


# -- alerts ordering ----------------------------------------------------------


def test_alerts_ordering_warning_before_caution(backend):
    backend.raise_alert("a.caution", "CAUTION MSG", "caution")
    backend.raise_alert("b.warning", "WARNING MSG", "warning")
    alerts = backend.alerts()
    levels = [a.level for a in alerts]
    assert levels.index("warning") < levels.index("caution")


# -- build_backend ----------------------------------------------------------


def test_build_backend_mock():
    b = build_backend({"backend": {"kind": "mock"}})
    assert isinstance(b, MockBackend)


def test_build_backend_unknown_kind_raises():
    with pytest.raises(ValueError):
        build_backend({"backend": {"kind": "not-a-real-backend"}})


def test_build_backend_homeassistant_missing_url_and_token_raises(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    with pytest.raises(ValueError):
        build_backend({"backend": {"kind": "homeassistant"}})


# -- websocket_url ----------------------------------------------------------


def test_websocket_url_http():
    assert websocket_url("http://ha.local:8123") == "ws://ha.local:8123/api/websocket"


def test_websocket_url_https():
    assert websocket_url("https://ha.local:8123") == "wss://ha.local:8123/api/websocket"


def test_websocket_url_already_ws():
    assert websocket_url("ws://ha.local:8123") == "ws://ha.local:8123/api/websocket"


def test_websocket_url_already_suffixed():
    assert websocket_url("http://ha.local:8123/api/websocket") == "ws://ha.local:8123/api/websocket"


def test_websocket_url_bad_scheme_raises():
    with pytest.raises(ValueError):
        websocket_url("ftp://ha.local")
