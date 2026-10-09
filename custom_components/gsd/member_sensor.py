"""GSD member sensors for points balance."""
from __future__ import annotations

import logging

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

_LOGGER = logging.getLogger(__name__)

# gsd_<entry_id>_member_<user_id>_points
_UID_SUFFIX = "_points"


def _uid_prefix(config_entry: ConfigEntry) -> str:
    """Return the unique_id prefix shared by this entry's member sensors."""
    return f"gsd_{config_entry.entry_id}_member_"


def _active_members(coordinator: DataUpdateCoordinator) -> list:
    """Return the circle's active members.

    Inactive rows are pending join requests, not departed members: the server
    creates them with is_active false and flips it on accept, and its member
    endpoint returns them unfiltered. Someone who has only asked to join has no
    points worth a sensor. A member who actually leaves has their row deleted,
    so they fall out of this list entirely.
    """
    if not coordinator.data:
        return []
    return [m for m in coordinator.data if m.is_active]


async def async_setup_member_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up member point balance sensors."""
    config = hass.data[DOMAIN][config_entry.entry_id]
    members_coordinator = config.get("members_coordinator")

    if not members_coordinator:
        _LOGGER.warning("Members coordinator not available")
        return

    members = _active_members(members_coordinator)
    entities = [
        GsdMemberPointsSensor(members_coordinator, config_entry, member)
        for member in members
    ]

    # Drop entities for members who are gone. Registry-driven rather than driven
    # by the in-memory set, which only holds members this process has seen: a
    # member who left while Home Assistant was stopped would never be in it and
    # their sensor would sit "unavailable" forever.
    if members_coordinator.data is not None:
        _cleanup_orphaned_member_sensors(
            hass, config_entry, {m.user_id for m in members}
        )

    _LOGGER.debug("Creating %d member point sensors", len(entities))
    if entities:
        async_add_entities(entities)

    _track_member_changes(
        hass, members_coordinator, config_entry, async_add_entities
    )


def _cleanup_orphaned_member_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    current_ids: set[int],
) -> None:
    """Remove registry entries for members who are no longer in the circle."""
    registry = er.async_get(hass)
    prefix = _uid_prefix(config_entry)

    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.domain != "sensor":
            continue
        if not entry.unique_id.startswith(prefix):
            continue
        if not entry.unique_id.endswith(_UID_SUFFIX):
            continue

        user_id_part = entry.unique_id[len(prefix):-len(_UID_SUFFIX)]
        try:
            user_id = int(user_id_part)
        except ValueError:
            _LOGGER.debug(
                "Skipping member sensor with unparsable unique_id: %s", entry.unique_id
            )
            continue

        if user_id not in current_ids:
            _LOGGER.debug("Removing orphaned points sensor for member %s", user_id)
            registry.async_remove(entry.entity_id)


def _track_member_changes(
    hass: HomeAssistant,
    coordinator: DataUpdateCoordinator,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Track coordinator updates and follow members joining and leaving."""
    known_ids: set[int] = {m.user_id for m in _active_members(coordinator)}

    @callback
    def _on_coordinator_update() -> None:
        nonlocal known_ids
        # A failed refresh leaves data None. Treating that as "everybody left"
        # would delete every sensor on a transient server error.
        if coordinator.data is None:
            return

        members = _active_members(coordinator)
        current_ids = {m.user_id for m in members}
        new_ids = current_ids - known_ids
        removed_ids = known_ids - current_ids

        if new_ids:
            _LOGGER.debug("New circle members: %s", sorted(new_ids))
            async_add_entities(
                GsdMemberPointsSensor(coordinator, config_entry, member)
                for member in members
                if member.user_id in new_ids
            )

        if removed_ids:
            registry = er.async_get(hass)
            prefix = _uid_prefix(config_entry)
            for user_id in removed_ids:
                unique_id = f"{prefix}{user_id}{_UID_SUFFIX}"
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
                if entity_id:
                    _LOGGER.debug("Removing points sensor for member %s", user_id)
                    registry.async_remove(entity_id)

        known_ids = current_ids

    coordinator.async_add_listener(_on_coordinator_update)


class GsdMemberPointsSensor(CoordinatorEntity, SensorEntity):
    """Sensor for a member's current points balance."""

    def __init__(self, coordinator, config_entry: ConfigEntry, member) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._member = member
        self._config_entry = config_entry
        self._attr_unique_id = (
            f"{_uid_prefix(config_entry)}{member.user_id}{_UID_SUFFIX}"
        )
        self._attr_icon = "mdi:star"
        self._attr_native_unit_of_measurement = "pts"

    @property
    def _member_data(self):
        """Find this sensor's member in the coordinator data."""
        if not self.coordinator.data:
            return None
        for member in self.coordinator.data:
            if member.user_id == self._member.user_id and member.is_active:
                return member
        return None

    @property
    def available(self) -> bool:
        """Return True if the member is still in the circle."""
        return self._member_data is not None and super().available

    @property
    def name(self) -> str:
        """Return the member name, following renames on the server."""
        member = self._member_data or self._member
        return f"{member.display_name or member.username} Points"

    @property
    def native_value(self) -> int | None:
        """Return the current points balance."""
        member = self._member_data
        return member.points if member else None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        member = self._member_data
        if not member:
            return {}
        return {
            "user_id": member.user_id,
            "points_redeemed": member.points_redeemed,
            "username": member.username,
            "role": member.role,
        }
