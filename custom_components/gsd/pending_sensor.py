"""GSD pending approvals sensor.

sensor.gsd_pending_approvals: the number of chore completions waiting for
approval, from GET /eapi/v1/chore/pending via the checkpoints coordinator (30 s).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Fields of each pending row published in the `items` attribute.
_ITEM_KEYS = ("choreId", "name", "checkpoint", "completedByName", "performedAt")


def _pending(coordinator: DataUpdateCoordinator) -> list | None:
    data = coordinator.data
    if not data or not data.get("pending_supported"):
        return None
    pending = data.get("pending")
    return pending if isinstance(pending, list) else None


async def async_setup_pending_sensor(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the pending approvals sensor once the server has answered for it.

    An old server (404) or a token that may not list approvals (403) gets no
    sensor. With no data yet (server down at startup) it is created on the
    first successful refresh.
    """
    config = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: DataUpdateCoordinator | None = config.get("checkpoints_coordinator")
    if coordinator is None:
        return

    created = False

    @callback
    def _maybe_create() -> None:
        nonlocal created
        if created or _pending(coordinator) is None:
            return
        created = True
        async_add_entities([GsdPendingApprovalsSensor(coordinator, config_entry)])

    _maybe_create()
    if not created:
        remove = coordinator.async_add_listener(_maybe_create)
        config_entry.async_on_unload(remove)


class GsdPendingApprovalsSensor(CoordinatorEntity, SensorEntity):
    """Count of chore completions waiting for approval."""

    _attr_has_entity_name = False

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"gsd_{config_entry.entry_id}_pending_approvals"
        # Fixed so automations can reference it; applied on first registration.
        self.entity_id = "sensor.gsd_pending_approvals"
        self._attr_name = "GSD Pending approvals"
        self._attr_icon = "mdi:clipboard-check-outline"
        self._attr_native_unit_of_measurement = "chores"

    @property
    def available(self) -> bool:
        return super().available and _pending(self.coordinator) is not None

    @property
    def native_value(self) -> int | None:
        pending = _pending(self.coordinator)
        return None if pending is None else len(pending)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pending = _pending(self.coordinator) or []
        return {
            "items": [
                {key: row.get(key) for key in _ITEM_KEYS}
                for row in pending
                if isinstance(row, dict)
            ]
        }
