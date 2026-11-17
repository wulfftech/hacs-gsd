# -*- coding: utf-8 -*-
"""Exercise the checkpoint / approval code against stubbed Home Assistant internals.

Run it with plain python, no dependencies and no Home Assistant:

    python tests/test_checkpoints.py

Covers binary_sensor.py (checkpoint sensors), pending_sensor.py, and the
api.py paths for completing a chore that needs approval, the full-object chore
update, bulk approve/reject and error surfacing. aiohttp is stubbed too, and
the HTTP session is a fake that records requests and replays canned answers.
"""

import asyncio
import json
import os
import re
import sys
import types

# ---- stubs ---------------------------------------------------------------------


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Entity:
    """Mirrors how HA's Entity reads _attr_* for the properties used here."""

    entity_id = None
    _attr_unique_id = None
    _attr_name = None

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return self._attr_name


class _CoordinatorEntity(_Entity):
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = None

    @property
    def available(self):
        return getattr(self.coordinator, "last_update_success", True)


class _HomeAssistantError(Exception):
    pass


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class _ClientError(Exception):
    pass


class _ClientResponseError(_ClientError):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


_mod("aiohttp", ClientError=_ClientError, ClientResponseError=_ClientResponseError,
     ContentTypeError=_ClientError, ClientTimeout=lambda total=None: total,
     ClientSession=object)
_mod("homeassistant")
_mod("homeassistant.components")
_mod("homeassistant.components.sensor", SensorEntity=_Entity)
_mod("homeassistant.components.binary_sensor", BinarySensorEntity=_Entity)
_mod("homeassistant.components.todo", TodoItem=object, TodoItemStatus=object)
_mod("homeassistant.config_entries", ConfigEntry=object)
_mod("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
_mod("homeassistant.exceptions", HomeAssistantError=_HomeAssistantError)
_mod("homeassistant.util", slugify=_slugify)
_mod("homeassistant.helpers")
_mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_mod("homeassistant.helpers.update_coordinator",
     CoordinatorEntity=_CoordinatorEntity, DataUpdateCoordinator=object)

_PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    os.pardir, "custom_components", "gsd")
_gsd = types.ModuleType("gsd")
_gsd.__path__ = [_PKG]
sys.modules["gsd"] = _gsd

import importlib  # noqa: E402

DOMAIN = importlib.import_module("gsd.const").DOMAIN
bs = importlib.import_module("gsd.binary_sensor")
ps = importlib.import_module("gsd.pending_sensor")
api = importlib.import_module("gsd.api")

FAILURES = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILURES.append(label)


# ---- fixtures ------------------------------------------------------------------


class Coordinator:
    def __init__(self, data):
        self.data = data
        self.listeners = []
        self.last_update_success = True

    def async_add_listener(self, fn):
        self.listeners.append(fn)
        return lambda: self.listeners.remove(fn)

    def push(self, data, ok=True):
        if ok:
            self.data = data
        self.last_update_success = ok
        for fn in list(self.listeners):
            fn()


class Entry:
    entry_id = "ENTRY1"

    def __init__(self):
        self.unloads = []

    def async_on_unload(self, fn):
        self.unloads.append(fn)


def checkpoints(kids, all_):
    return {
        "checkpoints_supported": True,
        "pending_supported": True,
        "pending": [],
        "checkpoints": {"date": "2026-11-23", "kids": kids, "all": all_},
    }


def kid(user_id, name, morning, afternoon="none", evening="none"):
    def cp(state):
        return {"state": state, "total": 3, "approved": 1, "pending": 2, "remaining": 0}
    return {"userId": user_id, "name": name,
            "checkpoints": {"morning": cp(morning), "afternoon": cp(afternoon),
                            "evening": cp(evening)}}


def setup(platform_setup, coordinator):
    added = []
    entry = Entry()
    hass = types.SimpleNamespace(data={DOMAIN: {entry.entry_id: {"checkpoints_coordinator": coordinator}}})
    asyncio.run(platform_setup(hass, entry, lambda ents: added.extend(ents)))
    return added, entry


def by_id(entities):
    return {e.entity_id: e for e in entities}


# ---- binary sensors ------------------------------------------------------------

print("binary_sensor")

coord = Coordinator(checkpoints(
    [kid(2, "Alex", "waiting", "done"), kid(3, "Sam Lee", "done")],
    {"morning": False, "afternoon": True, "evening": True},
))
added, entry = setup(bs.async_setup_entry, coord)
ents = by_id(added)

expected = {f"binary_sensor.gsd_all_kids_{c}_done" for c in ("morning", "afternoon", "evening")}
expected |= {f"binary_sensor.gsd_alex_{c}_done" for c in ("morning", "afternoon", "evening")}
expected |= {f"binary_sensor.gsd_sam_lee_{c}_done" for c in ("morning", "afternoon", "evening")}
check("entity ids are exactly the specified ones", set(ents) == expected)
check("unique ids are entry + user + checkpoint",
      ents["binary_sensor.gsd_alex_morning_done"].unique_id == "gsd_ENTRY1_checkpoint_2_morning"
      and ents["binary_sensor.gsd_all_kids_evening_done"].unique_id == "gsd_ENTRY1_checkpoint_all_evening")
check("unique ids are distinct", len({e.unique_id for e in added}) == len(added))

alex_m = ents["binary_sensor.gsd_alex_morning_done"]
alex_a = ents["binary_sensor.gsd_alex_afternoon_done"]
alex_e = ents["binary_sensor.gsd_alex_evening_done"]
check("waiting is off", alex_m.is_on is False)
check("done is on", alex_a.is_on is True)
check("none is on", alex_e.is_on is True)
attrs = alex_m.extra_state_attributes
check("kid attributes", attrs == {"state": "waiting", "total": 3, "approved": 1, "pending": 2,
                                  "remaining": 0, "user_id": 2, "date": "2026-11-23"})
check("kid friendly name", alex_m.name == "GSD Alex morning done")

all_m = ents["binary_sensor.gsd_all_kids_morning_done"]
all_a = ents["binary_sensor.gsd_all_kids_afternoon_done"]
check("all.morning false is off", all_m.is_on is False)
check("all.afternoon true is on", all_a.is_on is True)
check("all attributes carry per-kid states",
      all_m.extra_state_attributes == {"date": "2026-11-23",
                                       "kids": {"Alex": "waiting", "Sam Lee": "done"}})
check("available while the server answers", all(e.available for e in added))

coord.push(None, ok=False)
check("server unreachable: every sensor unavailable", not any(e.available for e in added))
coord.push(coord.data, ok=True)
check("server back: available again", all(e.available for e in added))

before = len(added)
coord.push(checkpoints(
    [kid(2, "Alex", "done"), kid(3, "Sam Lee", "done"), kid(4, "Zed", "todo")],
    {"morning": False, "afternoon": True, "evening": True},
))
new = by_id(added[before:])
check("new kid gets three sensors, nothing duplicated",
      set(new) == {f"binary_sensor.gsd_zed_{c}_done" for c in ("morning", "afternoon", "evening")})

coord.push(checkpoints([kid(2, "Alex", "done")], {"morning": True, "afternoon": True, "evening": True}))
check("kid gone from payload: their sensors unavailable",
      not ents["binary_sensor.gsd_sam_lee_morning_done"].available)
check("remaining kid still available", ents["binary_sensor.gsd_alex_morning_done"].available)

old = Coordinator({"checkpoints_supported": False, "checkpoints": None,
                   "pending_supported": False, "pending": None})
added_old, _ = setup(bs.async_setup_entry, old)
check("old server (404): no checkpoint entities", added_old == [])

late = Coordinator(None)
late.last_update_success = False
added_late, _ = setup(bs.async_setup_entry, late)
check("server down at startup: nothing yet", added_late == [])
late.push(checkpoints([kid(2, "Alex", "done")], {"morning": True, "afternoon": True, "evening": True}))
check("created on first successful refresh", len(added_late) == 6)

# ---- pending sensor ------------------------------------------------------------

print("pending_sensor")

pc = Coordinator({"checkpoints_supported": True, "checkpoints": None, "pending_supported": True,
                  "pending": [{"choreId": 12, "name": "Morning jobs", "checkpoint": "morning",
                               "points": 0, "assignedTo": 2, "completedBy": 2,
                               "completedByName": "Alex", "performedAt": "2026-11-23T00:12:00Z",
                               "dueDate": "2026-11-22T23:00:00Z", "note": None, "historyId": 345}]})
added_p, _ = setup(ps.async_setup_pending_sensor, pc)
check("one pending sensor", len(added_p) == 1)
sensor = added_p[0]
check("entity id", sensor.entity_id == "sensor.gsd_pending_approvals")
check("unique id", sensor.unique_id == "gsd_ENTRY1_pending_approvals")
check("state is the count", sensor.native_value == 1)
check("items carry the listed fields only",
      sensor.extra_state_attributes == {"items": [{
          "choreId": 12, "name": "Morning jobs", "checkpoint": "morning",
          "completedByName": "Alex", "performedAt": "2026-11-23T00:12:00Z"}]})
pc.push(None, ok=False)
check("unavailable when the server is down", not sensor.available)

added_p404, _ = setup(ps.async_setup_pending_sensor,
                      Coordinator({"pending_supported": False, "pending": None}))
check("no pending endpoint: no sensor", added_p404 == [])

# ---- api ---------------------------------------------------------------------

print("api")


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.reason = "reason"
        self._text = body if isinstance(body, str) else json.dumps(body)

    async def text(self):
        return self._text

    async def json(self):
        return json.loads(self._text)

    def raise_for_status(self):
        if self.status >= 400:
            raise _ClientResponseError(self.status)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = routes   # (method, path) -> (status, body)
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        path = url.split("http://gsd", 1)[1]
        self.calls.append((method, path, json))
        status, body = self.routes.get((method, path), (404, {"error": "not found"}))
        return FakeResponse(status, body)

    def get(self, url, headers=None, timeout=None):
        return self.request("GET", url, headers=headers, timeout=timeout)


CHORE = {"id": 7, "name": "Dishes", "description": "Wash up", "status": 0,
         "nextDueDate": "2026-11-23T09:00:00Z", "frequencyType": "daily",
         "checkpoint": "evening", "skipHolidays": True, "rotationOrder": [2, 3],
         "rotationAnchor": "2026-11-02T00:00:00Z", "assignedTo": 2}


def client(routes):
    session = FakeSession(routes)
    return api.GsdApiClient("http://gsd/", "tok", session), session


c, _ = client({("POST", "/eapi/v1/chore/7/complete"): (200, {
    "res": dict(CHORE, status=3), "message": "Chore completion submitted for approval"})})
task = asyncio.run(c.async_complete_task(7))
check("approval-wrapped completion parses", task.id == 7 and task.status == 3)

c, _ = client({("POST", "/eapi/v1/chore/7/complete"): (200, CHORE)})
check("bare completion parses", asyncio.run(c.async_complete_task(7)).name == "Dishes")

c, _ = client({("POST", "/eapi/v1/chore/7/complete"): (409, {"error": "Chore is already waiting for approval"})})
try:
    asyncio.run(c.async_complete_task(7))
    check("409 raises", False)
except api.GsdApiError as err:
    check("409 raises with the server's message",
          err.status == 409 and "Chore is already waiting for approval" in str(err))
check("GsdApiError is a HomeAssistantError", issubclass(api.GsdApiError, _HomeAssistantError))

c, s = client({("GET", "/eapi/v1/chore/7"): (200, CHORE),
               ("PUT", "/eapi/v1/chore/7"): (200, dict(CHORE, name="Dishes!"))})
asyncio.run(c.async_update_task(7, name="Dishes!"))
put = [call for call in s.calls if call[0] == "PUT"][0][2]
check("update sends the whole chore",
      all(put.get(k) == v for k, v in CHORE.items() if k != "name"))
check("update changes only the given field", put["name"] == "Dishes!")
check("update keeps the due date in dueDate", put["dueDate"] == CHORE["nextDueDate"])

c, s = client({("GET", "/eapi/v1/chore"): (200, [CHORE]),
               ("PUT", "/eapi/v1/chore/7"): (200, CHORE)})
asyncio.run(c.async_update_task(7, description=""))
put = [call for call in s.calls if call[0] == "PUT"][0][2]
check("update falls back to the list on an old server",
      put["checkpoint"] == "evening" and put["description"] == "")

c, s = client({("POST", "/eapi/v1/chore/approve"): (200, {"res": {
    "done": [12], "failed": [{"choreId": 14, "error": "Chore is not pending approval"}]}})})
res = asyncio.run(c.async_bulk_review("approve", chore_ids=[12, 14]))
check("approve body", s.calls[-1][2] == {"choreIds": [12, 14]})
check("approve result", res == {"done": [12], "failed": [{"choreId": 14, "error": "Chore is not pending approval"}]})
c, s = client({("POST", "/eapi/v1/chore/approve"): (200, {"res": {"done": [], "failed": []}})})
asyncio.run(c.async_bulk_review("approve", approve_all=True))
check("approve all body", s.calls[-1][2] == {"all": True})
c, s = client({("POST", "/eapi/v1/chore/reject"): (200, {"res": {"done": [12], "failed": []}})})
asyncio.run(c.async_bulk_review("reject", chore_ids=[12], note="have another go"))
check("reject body", s.calls[-1][2] == {"choreIds": [12], "note": "have another go"})

c, _ = client({})
try:
    asyncio.run(c.async_get_checkpoints())
    check("checkpoints 404 raises", False)
except api.GsdApiError as err:
    check("checkpoints 404 raises GsdApiError(404)", err.status == 404)

c, _ = client({("GET", "/eapi/v1/chore"): (200, {"unexpected": True})})
try:
    asyncio.run(c.async_get_tasks())
    check("malformed chore list raises", False)
except api.GsdApiError:
    check("malformed chore list raises instead of returning []", True)

c, _ = client({("GET", "/eapi/v1/chore"): (200, {"res": [CHORE]})})
check("res-wrapped chore list parses", len(asyncio.run(c.async_get_tasks())) == 1)

print()
print(f"FAILURES: {len(FAILURES)}")
sys.exit(1 if FAILURES else 0)
