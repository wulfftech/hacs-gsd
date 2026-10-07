"""Donetick member sensors for points balance."""
import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.sensor import SensorEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_member_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up member point balance sensors."""
    try:
        config = hass.data[DOMAIN][config_entry.entry_id]
        members_coordinator = config.get("members_coordinator")

        if not members_coordinator:
            _LOGGER.warning("Members coordinator not available")
            return

        entities = []
        if members_coordinator.data:
            for member in members_coordinator.data:
                entity = DonetickMemberPointsSensor(members_coordinator, config_entry, member)
                entities.append(entity)
                _LOGGER.debug("Created points sensor for %s", member.display_name)

        if entities:
            async_add_entities(entities)
            _LOGGER.debug("Added %d member point sensors", len(entities))

    except Exception as e:
        _LOGGER.error("Error setting up member sensors: %s", e)


class DonetickMemberPointsSensor(CoordinatorEntity, SensorEntity):
    """Sensor for a member's current points balance."""

    def __init__(self, coordinator, config_entry: ConfigEntry, member) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._member = member
        self._config_entry = config_entry
        self._attr_unique_id = f"dt_{config_entry.entry_id}_member_{member.user_id}_points"
        self._attr_name = f"{member.display_name} Points"
        self._attr_icon = "mdi:star"
        self._attr_native_unit_of_measurement = "pts"

    @property
    def native_value(self) -> int:
        """Return the current points balance."""
        if self.coordinator.data:
            for member in self.coordinator.data:
                if member.user_id == self._member.user_id:
                    return member.points
        return 0

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        if self.coordinator.data:
            for member in self.coordinator.data:
                if member.user_id == self._member.user_id:
                    return {
                        "points_redeemed": member.points_redeemed,
                        "username": member.username,
                    }
        return {}
