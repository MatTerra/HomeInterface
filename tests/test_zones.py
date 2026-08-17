"""Tests for the zone feature covering model, loader, and backend."""

from __future__ import annotations

import pytest

from homeinterface.backend.mock import MockBackend
from homeinterface.floorplan.loader import PlanError, dump_plan, plan_from_dict
from homeinterface.floorplan.model import Device, Floor, FloorPlan, Room, Zone, ZoneMember


# -- Parsing / Model Tests ---------------------------------------------------


def test_zone_members_with_bare_room_ids():
    """A plan dict with bare room ids (unique across the plan) resolves correctly."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "living", "rect": [0, 0, 5, 5]},
                    {"id": "kitchen", "rect": [5, 0, 5, 5]},
                ]
            },
            {
                "id": "f2",
                "rooms": [
                    {"id": "bedroom", "rect": [0, 0, 5, 5]},
                ]
            },
        ],
        "zones": [
            {
                "id": "social",
                "name": "Social Area",
                "rooms": ["living", "kitchen"],
            }
        ]
    })
    zone = plan.zone("social")
    assert zone is not None
    assert len(zone.members) == 2
    members_by_room = {m.room_id: m for m in zone.members}
    assert members_by_room["living"].floor_id == "f1"
    assert members_by_room["kitchen"].floor_id == "f1"


def test_zone_members_with_qualified_refs():
    """Qualified refs floor_id.room_id also resolve correctly."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [{"id": "living", "rect": [0, 0, 5, 5]}]
            },
            {
                "id": "f2",
                "rooms": [{"id": "living", "rect": [0, 0, 5, 5]}]
            },
        ],
        "zones": [
            {
                "id": "downstairs",
                "rooms": ["f1.living"],
            },
            {
                "id": "upstairs",
                "rooms": ["f2.living"],
            },
        ]
    })
    down = plan.zone("downstairs")
    up = plan.zone("upstairs")
    assert down.members[0].floor_id == "f1"
    assert up.members[0].floor_id == "f2"


def test_zone_spanning_floors():
    """A zone with members on two floors: spans_floors=True, floor_ids has both."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "ground",
                "rooms": [{"id": "stairwell", "rect": [0, 0, 2, 2]}]
            },
            {
                "id": "first",
                "rooms": [{"id": "stairwell", "rect": [0, 0, 2, 2]}]
            },
        ],
        "zones": [
            {
                "id": "stairs",
                "rooms": ["ground.stairwell", "first.stairwell"],
            }
        ]
    })
    zone = plan.zone("stairs")
    assert zone.spans_floors is True
    assert zone.floor_ids == ["ground", "first"]


def test_zone_single_floor_does_not_span():
    """A single-floor zone has spans_floors=False."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "r1", "rect": [0, 0, 5, 5]},
                    {"id": "r2", "rect": [5, 0, 5, 5]},
                ]
            }
        ],
        "zones": [
            {"id": "z1", "rooms": ["r1", "r2"]}
        ]
    })
    zone = plan.zone("z1")
    assert zone.spans_floors is False
    assert zone.floor_ids == ["f1"]


def test_zone_tag_uses_short_name_or_name():
    """Zone.tag uses short_name when set, else first 3 chars of name uppercased."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "r1", "rect": [0, 0, 5, 5]},
                    {"id": "r2", "rect": [5, 0, 5, 5]},
                ]
            }
        ],
        "zones": [
            {
                "id": "z1",
                "name": "Social Area",
                "short_name": "SOC",
                "rooms": ["r1"],
            },
            {
                "id": "z2",
                "name": "Kitchen Wing",
                "rooms": ["r2"],
            }
        ]
    })
    z1 = plan.zone("z1")
    z2 = plan.zone("z2")
    assert z1.tag == "SOC"
    assert z2.tag == "KIT"


def test_zone_of_returns_zone_or_none():
    """FloorPlan.zone_of returns the zone for a member room and None for unaffiliated."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "living", "rect": [0, 0, 5, 5]},
                    {"id": "solo", "rect": [5, 0, 5, 5]},
                ]
            }
        ],
        "zones": [
            {"id": "z1", "rooms": ["living"]}
        ]
    })
    # living is in zone z1
    zone = plan.zone_of("f1", "living")
    assert zone is not None
    assert zone.id == "z1"
    # solo is not in any zone
    assert plan.zone_of("f1", "solo") is None


def test_zone_lookup_by_id():
    """FloorPlan.zone(id) returns None for unknown id."""
    plan = plan_from_dict({
        "floors": [
            {"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}
        ],
        "zones": [
            {"id": "z1", "rooms": ["r1"]}
        ]
    })
    assert plan.zone("z1") is not None
    assert plan.zone("unknown") is None


def test_zones_on_floor():
    """FloorPlan.zones_on(floor_id) lists zones touching that floor."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "r1", "rect": [0, 0, 5, 5]},
                    {"id": "r2", "rect": [5, 0, 5, 5]},
                    {"id": "r4", "rect": [0, 5, 5, 5]},
                ]
            },
            {
                "id": "f2",
                "rooms": [
                    {"id": "r3", "rect": [0, 0, 5, 5]},
                    {"id": "r5", "rect": [5, 0, 5, 5]},
                ]
            },
        ],
        "zones": [
            {"id": "z1", "rooms": ["r1"]},  # only on f1
            {"id": "z2", "rooms": ["r2", "f2.r3"]},  # spans f1 and f2
            {"id": "z3", "rooms": ["r5"]},  # only on f2
        ]
    })
    f1_zones = plan.zones_on("f1")
    f2_zones = plan.zones_on("f2")
    assert {z.id for z in f1_zones} == {"z1", "z2"}
    assert {z.id for z in f2_zones} == {"z2", "z3"}


def test_zone_rooms_resolves_to_floor_room_pairs():
    """zone_rooms returns (Floor, Room) pairs."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "r1", "rect": [0, 0, 5, 5]},
                    {"id": "r2", "rect": [5, 0, 5, 5]},
                ]
            },
            {
                "id": "f2",
                "rooms": [{"id": "r3", "rect": [0, 0, 5, 5]}]
            },
        ],
        "zones": [
            {"id": "z1", "rooms": ["r1", "f2.r3"]}
        ]
    })
    zone = plan.zone("z1")
    rooms = plan.zone_rooms(zone)
    assert len(rooms) == 2
    assert rooms[0][0].id == "f1"
    assert rooms[0][1].id == "r1"
    assert rooms[1][0].id == "f2"
    assert rooms[1][1].id == "r3"


def test_zone_rooms_skips_missing_members():
    """zone_rooms skips members whose floor or room no longer exists."""
    # Build a plan with a bogus ZoneMember directly (can't be produced via loader)
    plan = FloorPlan(
        floors=[
            Floor(
                id="f1",
                name="F1",
                rooms=[Room(id="r1", name="R1", polygon=[(0, 0), (5, 0), (5, 5), (0, 5)])]
            ),
        ],
        zones=[
            Zone(
                id="z1",
                name="Z1",
                members=[
                    ZoneMember("f1", "r1"),  # exists
                    ZoneMember("f1", "missing"),  # missing room
                    ZoneMember("f2", "r1"),  # missing floor
                ]
            )
        ]
    )
    rooms = plan.zone_rooms(plan.zone("z1"))
    # Only the first member should be included
    assert len(rooms) == 1
    assert rooms[0][1].id == "r1"


def test_zone_devices_returns_devices_from_all_rooms():
    """zone_devices returns devices from every member room across floors."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}],
                "devices": [
                    {"entity_id": "light.l1", "at": [2.5, 2.5]},
                    {"entity_id": "light.l2", "at": [3.5, 3.5]},
                ]
            },
            {
                "id": "f2",
                "rooms": [{"id": "r2", "rect": [0, 0, 5, 5]}],
                "devices": [
                    {"entity_id": "light.l3", "at": [2.5, 2.5]},
                ]
            },
        ],
        "zones": [
            {"id": "z1", "rooms": ["r1", "f2.r2"]}
        ]
    })
    zone = plan.zone("z1")
    devices = plan.zone_devices(zone)
    entity_ids = {d.entity_id for d in devices}
    assert entity_ids == {"light.l1", "light.l2", "light.l3"}


def test_zone_area_sums_member_room_areas():
    """zone_area sums the member room areas."""
    plan = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "r1", "rect": [0, 0, 10, 10]},  # area = 100
                    {"id": "r2", "rect": [0, 0, 5, 5]},    # area = 25
                ]
            },
        ],
        "zones": [
            {"id": "z1", "rooms": ["r1", "r2"]}
        ]
    })
    zone = plan.zone("z1")
    area = plan.zone_area(zone)
    assert area == pytest.approx(125.0)


# -- Loader Validation Tests -------------------------------------------------


def test_zone_error_missing_rooms_list():
    """Zone with missing rooms list raises PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [{"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}],
            "zones": [{"id": "z1", "name": "Z1"}],
        })
    assert "needs a non-empty 'rooms' list" in str(exc_info.value)


def test_zone_error_empty_rooms_list():
    """Zone with empty rooms list raises PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [{"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}],
            "zones": [{"id": "z1", "rooms": []}],
        })
    assert "needs a non-empty 'rooms' list" in str(exc_info.value)


def test_zone_error_bare_room_id_matches_no_room():
    """Bare room id that matches no room raises PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [{"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}],
            "zones": [{"id": "z1", "rooms": ["unknown_room"]}],
        })
    assert "no room named 'unknown_room'" in str(exc_info.value)


def test_zone_error_bare_room_id_ambiguous():
    """Bare room id existing on multiple floors raises PlanError mentioning qualification."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [
                {"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]},
                {"id": "f2", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]},
            ],
            "zones": [{"id": "z1", "rooms": ["r1"]}],
        })
    exc_str = str(exc_info.value)
    assert "room id 'r1' exists on floors" in exc_str
    assert "qualify it as" in exc_str


def test_zone_error_qualified_ref_unknown_floor():
    """Qualified ref with unknown floor id raises PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [{"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}],
            "zones": [{"id": "z1", "rooms": ["unknown_floor.r1"]}],
        })
    assert "unknown floor 'unknown_floor'" in str(exc_info.value)


def test_zone_error_qualified_ref_unknown_room():
    """Qualified ref with unknown room on that floor raises PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [{"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}],
            "zones": [{"id": "z1", "rooms": ["f1.unknown_room"]}],
        })
    assert "floor 'f1' has no room 'unknown_room'" in str(exc_info.value)


def test_zone_error_duplicate_room_in_zone():
    """Same room listed twice within one zone raises PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [{"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}],
            "zones": [{"id": "z1", "rooms": ["r1", "r1"]}],
        })
    assert "listed twice" in str(exc_info.value)


def test_zone_error_duplicate_zone_ids():
    """Duplicate zone ids raise PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [{"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}],
            "zones": [
                {"id": "z1", "rooms": ["r1"]},
                {"id": "z1", "rooms": ["r1"]},
            ],
        })
    assert "duplicate zone ids" in str(exc_info.value)


def test_zone_error_same_room_in_two_zones():
    """Same room claimed by two different zones raises PlanError."""
    with pytest.raises(PlanError) as exc_info:
        plan_from_dict({
            "floors": [
                {
                    "id": "f1",
                    "rooms": [
                        {"id": "r1", "rect": [0, 0, 5, 5]},
                        {"id": "r2", "rect": [5, 0, 5, 5]},
                    ]
                }
            ],
            "zones": [
                {"id": "z1", "rooms": ["r1"]},
                {"id": "z2", "rooms": ["r1", "r2"]},
            ],
        })
    exc_str = str(exc_info.value)
    assert "is in both" in exc_str
    assert "z1" in exc_str
    assert "z2" in exc_str


# -- Round-Trip Tests --------------------------------------------------------


def test_round_trip_preserves_zones():
    """plan_from_dict(dump_plan(plan)) preserves zone ids, names, and members."""
    original = plan_from_dict({
        "floors": [
            {
                "id": "f1",
                "rooms": [
                    {"id": "r1", "rect": [0, 0, 5, 5]},
                    {"id": "r2", "rect": [5, 0, 5, 5]},
                ]
            },
            {
                "id": "f2",
                "rooms": [{"id": "r3", "rect": [0, 0, 5, 5]}]
            },
        ],
        "zones": [
            {
                "id": "zone1",
                "name": "Zone One",
                "short_name": "Z1",
                "rooms": ["r1", "f2.r3"],
            },
            {
                "id": "zone2",
                "name": "Zone Two",
                "rooms": ["r2"],
            },
        ],
    })

    dumped = dump_plan(original)
    restored = plan_from_dict(dumped)

    assert len(restored.zones) == 2
    z1 = restored.zone("zone1")
    z2 = restored.zone("zone2")

    assert z1 is not None
    assert z1.name == "Zone One"
    assert z1.short_name == "Z1"
    assert len(z1.members) == 2
    z1_members = {(m.floor_id, m.room_id) for m in z1.members}
    assert z1_members == {("f1", "r1"), ("f2", "r3")}

    assert z2 is not None
    assert z2.name == "Zone Two"
    assert len(z2.members) == 1
    assert z2.members[0] == ZoneMember("f1", "r2")


def test_plan_without_zones_key_loads_with_empty_list():
    """A plan with no zones: key loads fine with plan.zones == []."""
    plan = plan_from_dict({
        "floors": [
            {"id": "f1", "rooms": [{"id": "r1", "rect": [0, 0, 5, 5]}]}
        ]
    })
    assert plan.zones == []


# -- Backend Group Helpers Tests ----------------------------------------------


def test_group_state_counts_available_entities():
    """group_state counts only entities that exist AND are available."""
    backend = MockBackend(["light.a", "light.b", "light.c"])
    backend._publish_many([])  # clear initial state
    # Manually set up entities with known states
    from homeinterface.backend import Entity
    backend._publish(Entity("light.a", "on"))
    backend._publish(Entity("light.b", "off"))
    backend._publish(Entity("light.c", "unavailable"))

    # Only light.a and light.b are available; light.c is unavailable
    on, total = backend.group_state(["light.a", "light.b", "light.c", "light.unknown"])
    assert on == 1  # only light.a is on
    assert total == 2  # only light.a and light.b are counted


def test_toggle_group_all_off_turns_all_on():
    """toggle_group with all off turns every member on."""
    backend = MockBackend(["light.a", "light.b", "light.c"])
    # All lights start off
    backend.toggle_group(["light.a", "light.b", "light.c"])
    assert backend.get("light.a").state == "on"
    assert backend.get("light.b").state == "on"
    assert backend.get("light.c").state == "on"


def test_toggle_group_some_on_turns_all_off():
    """toggle_group with some on turns every member off (any-on → all-off rule)."""
    backend = MockBackend(["light.a", "light.b", "light.c"])
    backend.toggle("light.a")
    backend.toggle("light.b")
    # a and b are on, c is off
    backend.toggle_group(["light.a", "light.b", "light.c"])
    assert backend.get("light.a").state == "off"
    assert backend.get("light.b").state == "off"
    assert backend.get("light.c").state == "off"


def test_set_group_groups_calls_by_domain():
    """set_group groups calls by domain - one call per domain with list payload."""
    backend = MockBackend([
        "light.living",
        "light.kitchen",
        "switch.pump",
        "cover.gate",
    ])

    # Track top-level calls to backend.call (before recursion)
    calls = []
    original_call = backend.call
    call_depth = [0]  # use list to allow modification in nested function

    def track_call(domain, service, entity_id=None, **data):
        call_depth[0] += 1
        try:
            # Only record calls at depth 1 (top-level from set_group, before recursion)
            if call_depth[0] == 1:
                calls.append((domain, service, entity_id))
            return original_call(domain, service, entity_id, **data)
        finally:
            call_depth[0] -= 1

    backend.call = track_call

    backend.set_group(
        ["light.living", "light.kitchen", "switch.pump", "cover.gate"],
        on=True
    )

    # Should have 3 top-level calls: one per domain
    assert len(calls) == 3
    domains_called = {c[0] for c in calls}
    assert domains_called == {"light", "switch", "cover"}

    # Each domain should have a list of entity_ids
    for domain, service, entity_id in calls:
        assert isinstance(entity_id, list), f"Expected list for {domain}, got {type(entity_id)}"
        if domain == "light":
            assert set(entity_id) == {"light.living", "light.kitchen"}
        elif domain == "switch":
            assert entity_id == ["switch.pump"]
        elif domain == "cover":
            assert entity_id == ["cover.gate"]


def test_set_group_routes_cover_to_open_close():
    """set_group routes cover.* to open_cover/close_cover."""
    backend = MockBackend(["cover.gate"])
    backend.set_group(["cover.gate"], on=True)
    assert backend.get("cover.gate").state == "open"

    backend.set_group(["cover.gate"], on=False)
    assert backend.get("cover.gate").state == "closed"


def test_set_group_routes_light_switch_fan_to_turn_on_off():
    """set_group routes light/switch/fan to turn_on/turn_off."""
    backend = MockBackend(["light.l", "switch.s", "fan.f"])

    backend.set_group(["light.l", "switch.s", "fan.f"], on=True)
    assert backend.get("light.l").state == "on"
    assert backend.get("switch.s").state == "on"
    assert backend.get("fan.f").state == "on"

    backend.set_group(["light.l", "switch.s", "fan.f"], on=False)
    assert backend.get("light.l").state == "off"
    assert backend.get("switch.s").state == "off"
    assert backend.get("fan.f").state == "off"


def test_set_group_brightness_zero_turns_off():
    """set_group_brightness(ids, 0) turns lights off."""
    backend = MockBackend(["light.l1", "light.l2"])
    backend.toggle("light.l1")
    backend.toggle("light.l2")
    assert backend.get("light.l1").state == "on"
    assert backend.get("light.l2").state == "on"

    backend.set_group_brightness(["light.l1", "light.l2"], 0)
    assert backend.get("light.l1").state == "off"
    assert backend.get("light.l2").state == "off"


def test_set_group_brightness_sends_to_lights_only():
    """set_group_brightness sends brightness_pct to only light.* members; non-lights ignored."""
    backend = MockBackend(["light.l1", "light.l2", "switch.s"])

    backend.set_group_brightness(["light.l1", "light.l2", "switch.s"], 75)

    assert backend.get("light.l1").state == "on"
    assert backend.get("light.l1").attributes["brightness"] == pytest.approx(191, abs=1)
    assert backend.get("light.l2").state == "on"
    assert backend.get("light.l2").attributes["brightness"] == pytest.approx(191, abs=1)
    # switch.s is unaffected
    assert backend.get("switch.s").state == "off"


def test_set_group_brightness_no_op_when_no_lights():
    """set_group_brightness is a no-op when there are no light.* members."""
    backend = MockBackend(["switch.s", "cover.c"])
    # Should not raise an error
    backend.set_group_brightness(["switch.s", "cover.c"], 50)


def test_set_group_temperature_targets_climates_only():
    """set_group_temperature targets only climate.* members and is no-op when there are none."""
    backend = MockBackend(["climate.c1", "climate.c2", "light.l"])

    backend.set_group_temperature(["climate.c1", "climate.c2", "light.l"], 25.5)

    assert backend.get("climate.c1").attributes["temperature"] == 25.5
    assert backend.get("climate.c2").attributes["temperature"] == 25.5
    # light.l is unaffected
    assert backend.get("light.l").state == "off"

    # No-op with no climates
    backend.set_group_temperature(["light.l"], 20.0)
