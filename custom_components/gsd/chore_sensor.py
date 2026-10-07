"""GSD chore sensor entities."""
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN
from .model import GsdMember, GsdTask

_LOGGER = logging.getLogger(__name__)


async def async_setup_chore_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GSD chore sensor entities."""
    config = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = config["coordinator"]

    entities = []
    if coordinator.data:
        for task in coordinator.data:
            if task.is_active:
                entities.append(GsdChoreSensor(coordinator, config_entry, task.id))

    # Drop entities for chores that no longer exist. This has to be driven by the
    # entity registry rather than the in-memory known_ids set: known_ids only ever
    # holds chores this process has seen, so a chore deleted while Home Assistant
    # was stopped would never appear in it and its entity would linger as
    # "unavailable" forever. The registry is the only record that survives a restart.
    if coordinator.data is not None:
        _cleanup_orphaned_chore_sensors(
            hass,
            config_entry,
            {task.id for task in coordinator.data if task.is_active},
        )

    _LOGGER.debug("Creating %d chore sensor entities", len(entities))
    if entities:
        async_add_entities(entities)

    # Listen for coordinator updates to add/remove sensors as chores change
    _track_new_chores(hass, coordinator, config_entry, async_add_entities)


def _cleanup_orphaned_chore_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    current_ids: set[int],
) -> None:
    """Remove registry entries for chore sensors whose chore is gone."""
    registry = er.async_get(hass)
    prefix = f"gsd_chore_{config_entry.entry_id}_"

    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.domain != "sensor" or not entry.unique_id.startswith(prefix):
            continue

        task_id_part = entry.unique_id[len(prefix):]
        try:
            task_id = int(task_id_part)
        except ValueError:
            _LOGGER.debug(
                "Skipping chore sensor with unparsable unique_id: %s", entry.unique_id
            )
            continue

        if task_id not in current_ids:
            _LOGGER.debug("Removing orphaned sensor for chore %s", task_id)
            registry.async_remove(entry.entity_id)


def _track_new_chores(
    hass: HomeAssistant,
    coordinator: DataUpdateCoordinator,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Track coordinator updates and add sensors for new chores."""
    known_ids: set[int] = set()
    if coordinator.data:
        known_ids = {task.id for task in coordinator.data if task.is_active}

    @callback
    def _on_coordinator_update() -> None:
        nonlocal known_ids
        if not coordinator.data:
            return

        current_ids = {task.id for task in coordinator.data if task.is_active}
        new_ids = current_ids - known_ids
        removed_ids = known_ids - current_ids

        if new_ids:
            new_entities = [
                GsdChoreSensor(coordinator, config_entry, task_id)
                for task_id in new_ids
            ]
            async_add_entities(new_entities)

        if removed_ids:
            registry = er.async_get(hass)
            for task_id in removed_ids:
                unique_id = f"gsd_chore_{config_entry.entry_id}_{task_id}"
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
                if entity_id:
                    _LOGGER.debug("Removing sensor for inactive chore %s", task_id)
                    registry.async_remove(entity_id)

        known_ids = current_ids

    coordinator.async_add_listener(_on_coordinator_update)


class GsdChoreSensor(CoordinatorEntity, SensorEntity):
    """Sensor entity for a single GSD chore."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        config_entry: ConfigEntry,
        task_id: int,
    ) -> None:
        """Initialize the chore sensor."""
        super().__init__(coordinator)
        self._task_id = task_id
        self._config_entry = config_entry
        self._attr_unique_id = f"gsd_chore_{config_entry.entry_id}_{task_id}"

    @property
    def _task(self) -> GsdTask | None:
        """Find this sensor's task in the coordinator data."""
        if not self.coordinator.data:
            return None
        for task in self.coordinator.data:
            if task.id == self._task_id:
                return task
        return None

    @property
    def available(self) -> bool:
        """Return True if the task still exists."""
        return self._task is not None and super().available

    @property
    def name(self) -> str:
        """Return the chore name."""
        task = self._task
        return task.name if task else f"Chore {self._task_id}"

    @property
    def native_value(self) -> str | None:
        """Return the chore name as the sensor state."""
        task = self._task
        return task.name if task else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose chore metadata as attributes."""
        task = self._task
        if not task:
            return {}

        attrs = {
            "task_id": task.id,
            "assigned_to": self._resolve_user_name(task.assigned_to),
            "assigned_to_user_id": task.assigned_to,
            "next_due_date": task.next_due_date.isoformat() if task.next_due_date else None,
            "frequency_type": task.frequency_type,
            "frequency": task.frequency,
            "priority": task.priority,
            "labels": task.labels,
            "is_active": task.is_active,
            "description": task.description,
        }
        return attrs

    def _resolve_user_name(self, user_id: int | None) -> str | None:
        """Resolve a user ID to a display name using circle members."""
        if user_id is None:
            return None
        config = self.hass.data[DOMAIN].get(self._config_entry.entry_id, {})
        members: list[GsdMember] = config.get("circle_members", [])
        for member in members:
            if member.user_id == user_id:
                return member.display_name or member.username
        return str(user_id)

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"chores_{self._config_entry.entry_id}")},
            "name": "GSD Chores",
            "manufacturer": "GSD",
            "model": "Chores",
        }
