"""GSD reward sensors: catalog and pending redemptions."""
import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_reward_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the rewards catalog and pending-redemptions sensors."""
    try:
        config = hass.data[DOMAIN][config_entry.entry_id]
        coordinator = config.get("rewards_coordinator")
        if not coordinator:
            _LOGGER.debug("Rewards coordinator not available; skipping reward sensors")
            return
        async_add_entities([
            GsdRewardsCatalogSensor(coordinator, config_entry),
            GsdPendingRedemptionsSensor(coordinator, config_entry),
        ])
    except Exception as e:
        _LOGGER.error("Error setting up reward sensors: %s", e)


class _RewardsBase(CoordinatorEntity, SensorEntity):
    """Shared device info for reward sensors."""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"rewards_{self._config_entry.entry_id}")},
            "name": "GSD Rewards",
            "manufacturer": "GSD",
            "model": "Rewards",
        }


class GsdRewardsCatalogSensor(_RewardsBase):
    """Number of active rewards, with the full catalog as an attribute."""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"gsd_{config_entry.entry_id}_rewards_catalog"
        self._attr_name = "GSD Rewards Catalog"
        self._attr_icon = "mdi:gift"
        self._attr_native_unit_of_measurement = "rewards"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data or {}
        return len([r for r in data.get("rewards", []) if r.is_active])

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "rewards": [
                {"id": r.id, "name": r.name, "points": r.points,
                 "description": r.description, "is_active": r.is_active}
                for r in data.get("rewards", [])
            ]
        }


class GsdPendingRedemptionsSensor(_RewardsBase):
    """Number of pending redemptions, with details as an attribute."""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"gsd_{config_entry.entry_id}_pending_redemptions"
        self._attr_name = "GSD Pending Redemptions"
        self._attr_icon = "mdi:gift-open"
        self._attr_native_unit_of_measurement = "redemptions"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data or {}
        return len([r for r in data.get("redemptions", []) if r.status == "pending"])

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "pending": [
                {"id": r.id, "reward_name": r.reward_name, "points": r.points,
                 "member": r.display_name or r.username, "user_id": r.user_id,
                 "redeemed_at": r.redeemed_at}
                for r in data.get("redemptions", []) if r.status == "pending"
            ],
            "all_redemptions": [
                {"id": r.id, "reward_name": r.reward_name, "points": r.points,
                 "member": r.display_name or r.username, "status": r.status,
                 "redeemed_at": r.redeemed_at}
                for r in data.get("redemptions", [])
            ],
        }
