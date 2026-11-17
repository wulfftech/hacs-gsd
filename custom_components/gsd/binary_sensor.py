"""GSD checkpoint ("baseline done") binary sensors.

One sensor per kid per checkpoint, plus one per checkpoint for all kids, fed by
GET /eapi/v1/household/checkpoints through the checkpoints coordinator (30 s).

Entity ids are part of the contract with automations, so they are set
explicitly rather than left to Home Assistant's name-based derivation:

  binary_sensor.gsd_all_kids_<checkpoint>_done
  binary_sensor.gsd_<slugified kid name>_<checkpoint>_done

The id is only applied when the registry entry is first created; after that the
registry (keyed on unique_id) owns it, so a kid's rename on the server changes
the friendly name but not the entity id.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import slugify

from .const import DOMAIN, CHECKPOINTS

_LOGGER = logging.getLogger(__name__)

# A kid's checkpoint counts as done when every job is approved, or there are none.
_DONE_STATES = ("done", "none")


def _checkpoints_payload(coordinator: DataUpdateCoordinator) -> dict | None:
    """Return the checkpoints payload, or None if absent or unsupported."""
    data = coordinator.data
    if not data or not data.get("checkpoints_supported"):
        return None
    payload = data.get("checkpoints")
    return payload if isinstance(payload, dict) else None


def _kids(payload: dict | None) -> list[dict]:
    """Return the kid rows that carry a user id."""
    if not payload:
        return []
    return [
        kid
        for kid in payload.get("kids") or []
        if isinstance(kid, dict) and kid.get("userId") is not None
    ]


def _uid(config_entry: ConfigEntry, who: str, checkpoint: str) -> str:
    """gsd_<entry_id>_checkpoint_<user_id|all>_<checkpoint>."""
    return f"gsd_{config_entry.entry_id}_checkpoint_{who}_{checkpoint}"


def _kid_object_id(kid: dict, checkpoint: str) -> str:
    """Object id for a kid's sensor: gsd_<slug of name>_<checkpoint>_done."""
    slug = slugify(str(kid.get("name") or "")) or f"user_{kid['userId']}"
    return f"gsd_{slug}_{checkpoint}_done"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up checkpoint binary sensors.

    Entities are created from the coordinator's data, and again whenever new
    kids appear. With no data yet (server down at startup) or an old server
    (404), nothing is created now; the listener creates them once the server
    answers with checkpoints.
    """
    config = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: DataUpdateCoordinator | None = config.get("checkpoints_coordinator")
    if coordinator is None:
        return

    all_created = False
    known_kids: set[int] = set()

    @callback
    def _add_new_entities() -> None:
        nonlocal all_created
        payload = _checkpoints_payload(coordinator)
        if payload is None:
            return

        entities: list[BinarySensorEntity] = []
        if not all_created:
            all_created = True
            entities.extend(
                GsdAllKidsCheckpointSensor(coordinator, config_entry, checkpoint)
                for checkpoint in CHECKPOINTS
            )
        for kid in _kids(payload):
            user_id = kid["userId"]
            if user_id in known_kids:
                continue
            known_kids.add(user_id)
            entities.extend(
                GsdKidCheckpointSensor(coordinator, config_entry, kid, checkpoint)
                for checkpoint in CHECKPOINTS
            )

        if entities:
            _LOGGER.debug("Creating %d GSD checkpoint binary sensors", len(entities))
            async_add_entities(entities)

    _add_new_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class _GsdCheckpointBase(CoordinatorEntity, BinarySensorEntity):
    """Shared plumbing for checkpoint sensors."""

    # Names are spelled out in full ("GSD ... done") rather than composed from a
    # device name, and entity_id is set explicitly; see the module docstring.
    _attr_has_entity_name = False

    def __init__(self, coordinator, config_entry: ConfigEntry, checkpoint: str) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._checkpoint = checkpoint

    @property
    def _payload(self) -> dict | None:
        return _checkpoints_payload(self.coordinator)

    @property
    def available(self) -> bool:
        """Unavailable when the server is unreachable or no longer reports this."""
        return super().available and self._payload is not None


class GsdAllKidsCheckpointSensor(_GsdCheckpointBase):
    """On when every kid is done (or has nothing) for one checkpoint."""

    def __init__(self, coordinator, config_entry: ConfigEntry, checkpoint: str) -> None:
        super().__init__(coordinator, config_entry, checkpoint)
        self._attr_unique_id = _uid(config_entry, "all", checkpoint)
        self.entity_id = f"binary_sensor.gsd_all_kids_{checkpoint}_done"
        self._attr_name = f"GSD All kids {checkpoint} done"
        self._attr_icon = "mdi:account-group"

    @property
    def available(self) -> bool:
        payload = self._payload
        return (
            super().available
            and isinstance(payload.get("all"), dict)
            and self._checkpoint in payload["all"]
        )

    @property
    def is_on(self) -> bool | None:
        payload = self._payload
        if not payload or not isinstance(payload.get("all"), dict):
            return None
        value = payload["all"].get(self._checkpoint)
        return None if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = self._payload
        if not payload:
            return {}
        kids: dict[str, Any] = {}
        for kid in _kids(payload):
            cp = (kid.get("checkpoints") or {}).get(self._checkpoint) or {}
            kids[str(kid.get("name") or kid["userId"])] = cp.get("state")
        return {"date": payload.get("date"), "kids": kids}


class GsdKidCheckpointSensor(_GsdCheckpointBase):
    """On when one kid's checkpoint state is done or none."""

    def __init__(self, coordinator, config_entry: ConfigEntry, kid: dict, checkpoint: str) -> None:
        super().__init__(coordinator, config_entry, checkpoint)
        self._user_id = kid["userId"]
        self._initial_name = str(kid.get("name") or f"User {self._user_id}")
        self._attr_unique_id = _uid(config_entry, str(self._user_id), checkpoint)
        self.entity_id = f"binary_sensor.{_kid_object_id(kid, checkpoint)}"
        self._attr_icon = "mdi:check-circle-outline"

    @property
    def _kid(self) -> dict | None:
        for kid in _kids(self._payload):
            if kid["userId"] == self._user_id:
                return kid
        return None

    @property
    def _checkpoint_data(self) -> dict | None:
        kid = self._kid
        if not kid:
            return None
        cp = (kid.get("checkpoints") or {}).get(self._checkpoint)
        return cp if isinstance(cp, dict) else None

    @property
    def available(self) -> bool:
        return super().available and self._checkpoint_data is not None

    @property
    def name(self) -> str:
        """Follow renames on the server; the entity_id stays as first created."""
        kid = self._kid
        kid_name = str(kid.get("name")) if kid and kid.get("name") else self._initial_name
        return f"GSD {kid_name} {self._checkpoint} done"

    @property
    def is_on(self) -> bool | None:
        cp = self._checkpoint_data
        if cp is None:
            return None
        return cp.get("state") in _DONE_STATES

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cp = self._checkpoint_data
        if cp is None:
            return {}
        payload = self._payload or {}
        return {
            "state": cp.get("state"),
            "total": cp.get("total"),
            "approved": cp.get("approved"),
            "pending": cp.get("pending"),
            "remaining": cp.get("remaining"),
            "user_id": self._user_id,
            "date": payload.get("date"),
        }
