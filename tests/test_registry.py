"""Tests for the plan -> Home Assistant registry mapping.

The mapping is: pavimento -> HA floor, zona -> HA area, cômodo -> HA label.
"""

from __future__ import annotations

from homeinterface.backend.registry import registry_plan
from homeinterface.floorplan.loader import dump_plan, plan_from_dict

TWO_ROOM_ZONE = {
    "floors": [
        {
            "id": "terreo",
            "name": "Térreo",
            "rooms": [
                {"id": "sala", "name": "Sala", "rect": [0, 0, 5, 5]},
                {"id": "copa", "name": "Copa", "rect": [5, 0, 5, 5]},
                {"id": "hall", "name": "Hall", "rect": [0, 5, 10, 2]},
            ],
            "devices": [
                {"entity_id": "light.sala", "at": [2, 2]},
                {"entity_id": "climate.sala", "at": [1, 1]},
                {"entity_id": "light.copa", "at": [7, 2]},
                {"entity_id": "light.hall", "at": [5, 6]},
            ],
        }
    ],
    "zones": [{"id": "social", "name": "Área Social", "rooms": ["sala", "copa"]}],
}


def test_zone_becomes_area_and_room_becomes_label():
    reg = registry_plan(plan_from_dict(TWO_ROOM_ZONE))

    assert reg.floors == ("Térreo",)
    areas = {a.name: a.floor for a in reg.areas}
    assert areas["Área Social"] == "Térreo"
    assert set(reg.labels) == {"Sala", "Copa", "Hall"}

    placed = {e.entity_id: (e.area, e.labels) for e in reg.entities}
    assert placed["light.sala"] == ("Área Social", ("Sala",))
    assert placed["climate.sala"] == ("Área Social", ("Sala",))
    assert placed["light.copa"] == ("Área Social", ("Copa",))


def test_room_outside_any_zone_still_gets_its_own_area():
    """Otherwise the hall lamp would land in no HA area at all."""
    reg = registry_plan(plan_from_dict(TWO_ROOM_ZONE))
    areas = {a.name for a in reg.areas}
    assert "Hall" in areas
    placed = {e.entity_id: e.area for e in reg.entities}
    assert placed["light.hall"] == "Hall"


def test_ha_name_overrides_win_over_display_names():
    reg = registry_plan(plan_from_dict({
        "floors": [
            {
                "id": "terreo",
                "name": "Térreo",
                "ha_floor": "Ground Floor",
                "rooms": [{"id": "sala", "name": "Sala", "ha_label": "Living Room",
                           "rect": [0, 0, 5, 5]}],
                "devices": [{"entity_id": "light.sala", "at": [2, 2]}],
            }
        ],
        "zones": [{"id": "social", "name": "Área Social", "ha_area": "Social",
                   "rooms": ["sala"]}],
    }))

    assert reg.floors == ("Ground Floor",)
    assert [(a.name, a.floor) for a in reg.areas] == [("Social", "Ground Floor")]
    assert reg.labels == ("Living Room",)
    assert reg.entities[0].area == "Social"
    assert reg.entities[0].labels == ("Living Room",)


def test_device_in_no_room_is_reported_not_assigned():
    reg = registry_plan(plan_from_dict({
        "floors": [
            {
                "id": "terreo",
                "rooms": [{"id": "sala", "rect": [0, 0, 5, 5]}],
                "devices": [{"entity_id": "sensor.quadro", "at": [50, 50]}],
            }
        ],
    }))
    assert reg.entities == ()
    assert any("no room" in note for note in reg.notes)


def test_zone_spanning_floors_picks_one_floor_and_says_so():
    reg = registry_plan(plan_from_dict({
        "floors": [
            {"id": "terreo", "name": "Térreo", "level": 0,
             "rooms": [{"id": "escada_baixo", "rect": [0, 0, 2, 2]}]},
            {"id": "superior", "name": "Superior", "level": 1,
             "rooms": [{"id": "escada_cima", "rect": [0, 0, 2, 2]}]},
        ],
        "zones": [{"id": "escada", "name": "Escada",
                   "rooms": ["escada_baixo", "escada_cima"]}],
    }))
    stair = [a for a in reg.areas if a.name == "Escada"]
    assert len(stair) == 1
    assert stair[0].floor in ("Térreo", "Superior")
    assert any("spans" in note for note in reg.notes)


def test_ha_names_survive_a_dump_round_trip():
    source = {
        "floors": [
            {
                "id": "terreo",
                "ha_floor": "Ground Floor",
                "rooms": [{"id": "sala", "ha_label": "Living Room", "rect": [0, 0, 5, 5]}],
            }
        ],
        "zones": [{"id": "social", "ha_area": "Social", "rooms": ["sala"]}],
    }
    again = plan_from_dict(dump_plan(plan_from_dict(source)))
    assert again.floors[0].ha_floor == "Ground Floor"
    assert again.floors[0].rooms[0].ha_label == "Living Room"
    assert again.zone("social").ha_area == "Social"


def test_house_plan_maps_cleanly():
    """The real plan must produce one area per zone and no orphan lights."""
    from homeinterface.floorplan import load_plan

    reg = registry_plan(load_plan("config/floorplan.yaml"))
    areas = {a.name for a in reg.areas}
    assert {"Suíte", "Quarto", "Área Social", "Setor Íntimo", "Serviços"} <= areas

    placed = {e.entity_id: e.area for e in reg.entities}
    assert placed["light.closet1"] == "Suíte"
    assert placed["light.banho1"] == "Suíte"
    assert placed["light.closet3"] == "Quarto"
    assert placed["light.quarto3"] == "Quarto"
    assert placed["light.escritorio"] == "Setor Íntimo"
    assert all(area for area in placed.values())

    # Two rooms sharing a name would collapse into one HA label, so the plan
    # must never contain a duplicate - this is what forced "Hall" (the entrance)
    # and "Corredor" (the passage) apart.
    labels = list(reg.labels)
    assert len(labels) == len({name.strip().lower() for name in labels})

    # Every device the plan pins to a room must land somewhere.
    assert not reg.notes


# -- reconciliation against a fake Home Assistant ---------------------------


class FakeHomeAssistant:
    """Minimal in-memory stand-in for HA's registry websocket commands."""

    def __init__(self, areas=None, labels=None, entities=None, floors=None):
        self.floors = list(floors or [])
        self.areas = list(areas or [])
        self.labels = list(labels or [])
        self.entities = list(entities or [])
        self.updates = []
        self._seq = 0

    def _new_id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq}"

    async def request(self, _ws, type_, **payload):
        if type_ == "config/floor_registry/list":
            return self.floors
        if type_ == "config/area_registry/list":
            return self.areas
        if type_ == "config/label_registry/list":
            return self.labels
        if type_ == "config/entity_registry/list":
            return self.entities
        if type_ == "config/floor_registry/create":
            row = {"floor_id": self._new_id("floor"), "name": payload["name"]}
            self.floors.append(row)
            return row
        if type_ == "config/area_registry/create":
            row = {"area_id": self._new_id("area"), "name": payload["name"],
                   "floor_id": payload.get("floor_id")}
            self.areas.append(row)
            return row
        if type_ == "config/label_registry/create":
            row = {"label_id": self._new_id("label"), "name": payload["name"]}
            self.labels.append(row)
            return row
        if type_ in ("config/area_registry/update", "config/entity_registry/update"):
            self.updates.append((type_, payload))
            return payload
        raise AssertionError(f"unexpected command {type_}")


def _backend(plan_dict, fake):
    from homeinterface.backend.homeassistant import HomeAssistantBackend

    backend = HomeAssistantBackend(
        "http://ha.local:8123", "token",
        registry=registry_plan(plan_from_dict(plan_dict)),
    )
    backend._request = fake.request
    return backend


def _run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


def test_sync_creates_missing_floors_areas_and_labels():
    fake = FakeHomeAssistant(entities=[
        {"entity_id": "light.sala"}, {"entity_id": "climate.sala"},
        {"entity_id": "light.copa"}, {"entity_id": "light.hall"},
    ])
    backend = _backend(TWO_ROOM_ZONE, fake)
    _run(backend._sync_registry(object()))

    assert [f["name"] for f in fake.floors] == ["Térreo"]
    assert {a["name"] for a in fake.areas} == {"Área Social", "Hall"}
    assert {l["name"] for l in fake.labels} == {"Sala", "Copa", "Hall"}
    filed = {p["entity_id"]: p for _t, p in fake.updates}
    assert filed["light.sala"]["area_id"] == filed["climate.sala"]["area_id"]
    assert filed["light.sala"]["area_id"] != filed["light.hall"]["area_id"]


def test_sync_reuses_existing_names_case_insensitively():
    fake = FakeHomeAssistant(
        floors=[{"floor_id": "f_ground", "name": "térreo"}],
        areas=[{"area_id": "a_social", "name": "ÁREA SOCIAL", "floor_id": "f_ground"}],
        labels=[{"label_id": "l_sala", "name": "sala"}],
        entities=[{"entity_id": "light.sala", "area_id": "a_social", "labels": ["l_sala"]}],
    )
    backend = _backend(TWO_ROOM_ZONE, fake)
    _run(backend._sync_registry(object()))

    assert len(fake.floors) == 1
    assert sum(1 for a in fake.areas if a["name"].lower() == "área social") == 1
    assert sum(1 for l in fake.labels if l["name"].lower() == "sala") == 1
    # already correct, so nothing was rewritten for it
    assert "light.sala" not in {p.get("entity_id") for _t, p in fake.updates}


def test_sync_keeps_hand_made_labels_and_swaps_only_plan_labels():
    fake = FakeHomeAssistant(
        labels=[{"label_id": "l_sala", "name": "Sala"},
                {"label_id": "l_copa", "name": "Copa"},
                {"label_id": "l_hall", "name": "Hall"}],
        entities=[{"entity_id": "light.sala", "labels": ["l_copa", "l_favorita"]}],
    )
    backend = _backend(TWO_ROOM_ZONE, fake)
    _run(backend._sync_registry(object()))

    filed = {p["entity_id"]: p for _t, p in fake.updates}
    labels = set(filed["light.sala"]["labels"])
    assert "l_favorita" in labels   # foreign label survives
    assert "l_sala" in labels       # correct room label added
    assert "l_copa" not in labels   # wrong room label retired


def test_sync_moves_an_area_to_the_floor_the_plan_says():
    fake = FakeHomeAssistant(
        floors=[{"floor_id": "f_wrong", "name": "Superior"}],
        areas=[{"area_id": "a_social", "name": "Área Social", "floor_id": "f_wrong"}],
    )
    backend = _backend(TWO_ROOM_ZONE, fake)
    _run(backend._sync_registry(object()))

    moves = [p for t, p in fake.updates if t == "config/area_registry/update"]
    assert moves and moves[0]["area_id"] == "a_social"
    ground = next(f["floor_id"] for f in fake.floors if f["name"] == "Térreo")
    assert moves[0]["floor_id"] == ground


def test_sync_never_deletes_anything_it_did_not_plan():
    fake = FakeHomeAssistant(
        areas=[{"area_id": "a_garagem", "name": "Garagem", "floor_id": None}],
        labels=[{"label_id": "l_ferias", "name": "Férias"}],
        entities=[{"entity_id": "light.garagem", "area_id": "a_garagem"}],
    )
    backend = _backend(TWO_ROOM_ZONE, fake)
    _run(backend._sync_registry(object()))

    assert any(a["area_id"] == "a_garagem" for a in fake.areas)
    assert any(l["label_id"] == "l_ferias" for l in fake.labels)
    assert "light.garagem" not in {p.get("entity_id") for _t, p in fake.updates}


def test_sync_failure_raises_an_alert_instead_of_killing_the_link():
    from homeinterface.backend.homeassistant import HomeAssistantBackend

    async def refuse(_ws, _type, **_payload):
        raise RuntimeError("unauthorized")

    backend = HomeAssistantBackend(
        "http://ha.local:8123", "token",
        registry=registry_plan(plan_from_dict(TWO_ROOM_ZONE)),
    )
    backend._request = refuse
    _run(backend._sync_registry(object()))

    assert any(a.key == "ha.registry" for a in backend.alerts())


def test_entity_absent_from_the_ha_registry_is_flagged_not_fatal():
    fake = FakeHomeAssistant(entities=[{"entity_id": "light.sala"}])
    backend = _backend(TWO_ROOM_ZONE, fake)
    _run(backend._sync_registry(object()))

    assert any(a.key == "ha.registry.missing" for a in backend.alerts())
    assert "light.sala" in {p.get("entity_id") for _t, p in fake.updates}
