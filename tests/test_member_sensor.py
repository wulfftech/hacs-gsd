# -*- coding: utf-8 -*-
"""Exercise member_sensor.py against stubbed Home Assistant internals.

Run it with plain python, no dependencies and no Home Assistant:

    python tests/test_member_sensor.py

It stands in the handful of homeassistant modules member_sensor imports and
drives the real code, so the membership-tracking logic can be checked without
deploying to the box and restarting.
"""

import asyncio
import os
import sys
import types
from dataclasses import dataclass

# ---- stub out the homeassistant packages member_sensor imports ----------------


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Sensor:
    pass


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = None

    @property
    def available(self):
        return getattr(self.coordinator, "last_update_success", True)


def _callback(fn):
    return fn


class Registry:
    """Minimal entity registry: unique_id -> entity_id, per config entry."""

    def __init__(self):
        self.entries = {}   # entity_id -> SimpleEntry
        self.removed = []

    def add(self, entry_id, domain, unique_id, entity_id):
        self.entries[entity_id] = types.SimpleNamespace(
            entity_id=entity_id, domain=domain, unique_id=unique_id,
            config_entry_id=entry_id)

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.entries.pop(entity_id, None)

    def async_get_entity_id(self, domain, platform, unique_id):
        for e in self.entries.values():
            if e.domain == domain and e.unique_id == unique_id:
                return e.entity_id
        return None


REGISTRY = Registry()

_mod("homeassistant")
_mod("homeassistant.components")
_mod("homeassistant.components.sensor", SensorEntity=_Sensor)
_mod("homeassistant.config_entries", ConfigEntry=object)
_mod("homeassistant.core", HomeAssistant=object, callback=_callback)
_mod("homeassistant.helpers")
_mod(
    "homeassistant.helpers.entity_registry",
    async_get=lambda hass: REGISTRY,
    async_entries_for_config_entry=lambda reg, entry_id: [
        e for e in list(reg.entries.values()) if e.config_entry_id == entry_id
    ],
)
_mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_mod(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=_CoordinatorEntity,
    DataUpdateCoordinator=object,
)

# Register a bare "gsd" package pointing at the real directory, so submodules
# import without running __init__.py (which pulls in aiohttp and voluptuous).
_PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    os.pardir, "custom_components", "gsd")
_gsd = types.ModuleType("gsd")
_gsd.__path__ = [_PKG]
sys.modules["gsd"] = _gsd

import importlib  # noqa: E402

DOMAIN = importlib.import_module("gsd.const").DOMAIN
ms = importlib.import_module("gsd.member_sensor")

ENTRY = "ENTRY1"


@dataclass
class Member:
    user_id: int
    username: str
    display_name: str
    is_active: bool = True
    points: int = 0
    points_redeemed: int = 0
    role: str = "member"


class Coordinator:
    def __init__(self, data):
        self.data = data
        self.listeners = []
        self.last_update_success = True

    def async_add_listener(self, fn):
        self.listeners.append(fn)

    def push(self, data):
        self.data = data
        for fn in self.listeners:
            fn()


class Entry:
    entry_id = ENTRY
    options = {}
    data = {}


def uid(user_id):
    return f"gsd_{ENTRY}_member_{user_id}_points"


def run(members):
    """Set up the platform and return (coordinator, added entities)."""
    global REGISTRY
    REGISTRY = Registry()
    ms.er.async_get = lambda hass: REGISTRY
    ms.er.async_entries_for_config_entry = lambda reg, eid: [
        e for e in list(reg.entries.values()) if e.config_entry_id == eid
    ]

    coord = Coordinator(members)
    hass = types.SimpleNamespace(data={DOMAIN: {ENTRY: {"members_coordinator": coord}}})
    added = []

    def add_entities(ents):
        ents = list(ents)
        added.extend(ents)
        for e in ents:
            REGISTRY.add(ENTRY, "sensor", e._attr_unique_id,
                         "sensor." + e._attr_unique_id)

    asyncio.run(ms.async_setup_member_sensors(hass, Entry(), add_entities))
    return coord, added, hass


FAILS = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


print("1. active members get sensors, pending join requests do not")
coord, added, _ = run([
    Member(1, "shannon", "Shannon", points=370),
    Member(2, "ben", "Ben", points=805),
    Member(9, "newkid", "New Kid", is_active=False),   # pending join request
])
ids = sorted(e._attr_unique_id for e in added)
check("two sensors created", len(added) == 2)
check("pending member excluded", uid(9) not in ids)
check("unique_id shape", ids[0] == uid(1))

print("2. a member joining mid-run gets a sensor")
before = len(added)
coord.push([
    Member(1, "shannon", "Shannon", points=370),
    Member(2, "ben", "Ben", points=805),
    Member(3, "jo", "Joanna", points=730),
])
check("one sensor added", len(added) - before == 1)
check("it is Joanna's", added[-1]._attr_unique_id == uid(3))

print("3. accepting a pending request adds the sensor")
coord2, added2, _ = run([Member(1, "a", "A"), Member(9, "p", "P", is_active=False)])
check("pending not created at setup", len(added2) == 1)
coord2.push([Member(1, "a", "A"), Member(9, "p", "P", is_active=True)])
check("created once accepted", len(added2) == 2 and added2[-1]._attr_unique_id == uid(9))

print("4. a member leaving removes the sensor")
coord3, added3, _ = run([Member(1, "a", "A"), Member(2, "b", "B")])
REGISTRY.removed.clear()
coord3.push([Member(1, "a", "A")])            # row hard-deleted server-side
check("sensor removed", REGISTRY.removed == ["sensor." + uid(2)])

print("5. a transient failure does not delete everyone")
coord4, added4, _ = run([Member(1, "a", "A"), Member(2, "b", "B")])
REGISTRY.removed.clear()
coord4.push(None)
check("nothing removed on data=None", REGISTRY.removed == [])
coord4.push([Member(1, "a", "A"), Member(2, "b", "B")])
check("no duplicates after recovery", len(added4) == 2)

print("6. startup cleans up a member who left while HA was stopped")
REGISTRY = Registry()
ms.er.async_get = lambda hass: REGISTRY
ms.er.async_entries_for_config_entry = lambda reg, eid: [
    e for e in list(reg.entries.values()) if e.config_entry_id == eid
]
# stale entry restored by HA, plus neighbours that must survive
REGISTRY.add(ENTRY, "sensor", uid(5), "sensor.gone_points")
REGISTRY.add(ENTRY, "sensor", f"gsd_{ENTRY}_rewards_catalog", "sensor.rewards")
REGISTRY.add(ENTRY, "sensor", f"gsd_chore_{ENTRY}_12", "sensor.chore12")
REGISTRY.add("OTHER", "sensor", uid(7), "sensor.other_entry")
REGISTRY.add(ENTRY, "todo", uid(8), "todo.weird")

coord5 = Coordinator([Member(1, "a", "A")])
hass5 = types.SimpleNamespace(data={DOMAIN: {ENTRY: {"members_coordinator": coord5}}})
asyncio.run(ms.async_setup_member_sensors(
    hass5, Entry(),
    lambda ents: [REGISTRY.add(ENTRY, "sensor", e._attr_unique_id,
                               "sensor." + e._attr_unique_id) for e in list(ents)]))
check("stale member sensor removed", "sensor.gone_points" in REGISTRY.removed)
check("rewards sensor untouched", "sensor.rewards" not in REGISTRY.removed)
check("chore sensor untouched", "sensor.chore12" not in REGISTRY.removed)
check("other config entry untouched", "sensor.other_entry" not in REGISTRY.removed)
check("non-sensor domain untouched", "todo.weird" not in REGISTRY.removed)

print("7. a malformed unique_id is skipped, not crashed on")
REGISTRY = Registry()
ms.er.async_get = lambda hass: REGISTRY
ms.er.async_entries_for_config_entry = lambda reg, eid: [
    e for e in list(reg.entries.values()) if e.config_entry_id == eid
]
REGISTRY.add(ENTRY, "sensor", f"gsd_{ENTRY}_member_notanint_points", "sensor.bad")
coord6 = Coordinator([Member(1, "a", "A")])
hass6 = types.SimpleNamespace(data={DOMAIN: {ENTRY: {"members_coordinator": coord6}}})
try:
    asyncio.run(ms.async_setup_member_sensors(hass6, Entry(), lambda e: list(e)))
    check("no crash", True)
    check("malformed entry left alone", "sensor.bad" not in REGISTRY.removed)
except Exception as e:  # noqa: BLE001
    check("no crash (%s)" % e, False)

print("8. entity behaviour follows the member")
coord7 = Coordinator([Member(2, "ben", "Ben", points=805, points_redeemed=50)])
s = ms.GsdMemberPointsSensor(coord7, Entry(), coord7.data[0])
check("value", s.native_value == 805)
check("name", s.name == "Ben Points")
check("available", s.available is True)
check("attrs", s.extra_state_attributes["points_redeemed"] == 50)
coord7.data = [Member(2, "ben", "Benjamin", points=900)]
check("follows rename", s.name == "Benjamin Points")
coord7.data = []
check("unavailable once gone", s.available is False)
check("value None once gone", s.native_value is None)
check("attrs empty once gone", s.extra_state_attributes == {})
coord7.data = [Member(2, "ben", "Ben", is_active=False)]
check("inactive counts as gone", s.available is False)
s2 = ms.GsdMemberPointsSensor(coord7, Entry(), Member(3, "x", ""))
check("blank display_name falls back to username", s2.name == "x Points")

print()
print("FAILURES: %d" % len(FAILS))
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
