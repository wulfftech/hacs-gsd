"""The GSD integration."""
import asyncio
import logging
from datetime import timedelta
from typing import Awaitable, Callable

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import (
    DOMAIN,
    CONF_URL,
    CONF_TOKEN,
    CONF_SHOW_DUE_IN,
    CONF_REFRESH_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
    CHECKPOINT_REFRESH_INTERVAL,
    CHECKPOINT_UNSUPPORTED_REFRESH_INTERVAL,
)
from .api import GsdApiClient, GsdApiError, CHORE_STATUS_PENDING_APPROVAL

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [
    Platform.TODO,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.CALENDAR,
]


SERVICE_COMPLETE_TASK = "complete_task"
SERVICE_CREATE_TASK = "create_task"
SERVICE_UPDATE_TASK = "update_task"
SERVICE_DELETE_TASK = "delete_task"
SERVICE_SKIP_TASK = "skip_task"
SERVICE_APPROVE_CHORES = "approve_chores"
SERVICE_REJECT_CHORES = "reject_chores"

ALL_SERVICES = [
    SERVICE_COMPLETE_TASK,
    SERVICE_CREATE_TASK,
    SERVICE_UPDATE_TASK,
    SERVICE_DELETE_TASK,
    SERVICE_SKIP_TASK,
    SERVICE_APPROVE_CHORES,
    SERVICE_REJECT_CHORES,
]

COMPLETE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("completed_by"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

CREATE_TASK_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("due_date"): cv.string,
    vol.Optional("created_by"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

UPDATE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("due_date"): cv.string,
    vol.Optional("config_entry_id"): cv.string,
})

DELETE_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

SKIP_TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): vol.Coerce(int),
    vol.Optional("completed_by"): vol.Coerce(int),
    vol.Optional("config_entry_id"): cv.string,
})

APPROVE_CHORES_SCHEMA = vol.Schema({
    vol.Optional("chore_ids"): vol.All(cv.ensure_list, [vol.Coerce(int)]),
    vol.Optional("all", default=False): cv.boolean,
    vol.Optional("config_entry_id"): cv.string,
})

REJECT_CHORES_SCHEMA = vol.Schema({
    vol.Required("chore_ids"): vol.All(cv.ensure_list, [vol.Coerce(int)]),
    vol.Optional("note"): cv.string,
    vol.Optional("config_entry_id"): cv.string,
})


def _coordinator_update(
    fetch: Callable[[], Awaitable], what: str
) -> Callable[[], Awaitable]:
    """Wrap an API fetch so any failure surfaces as UpdateFailed.

    The coordinator then logs it once, keeps its last data, and flips
    last_update_success off, which makes every CoordinatorEntity on it
    unavailable until the server answers again.
    """

    async def _update():
        try:
            return await fetch()
        except UpdateFailed:
            raise
        except (GsdApiError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error fetching GSD {what}: {err}") from err

    return _update


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up GSD from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    client = GsdApiClient(
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
        session,
    )

    refresh_interval_seconds = entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="gsd_chores",
        update_method=_coordinator_update(client.async_get_tasks, "chores"),
        update_interval=timedelta(seconds=refresh_interval_seconds),
    )
    await coordinator.async_config_entry_first_refresh()

    # Circle members, for point balances and for resolving user IDs to names.
    # Supplementary data: the circle endpoint needs a Plus membership server-side,
    # so a failure here must not take the whole entry down with it.
    members_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="gsd_members",
        update_method=_coordinator_update(client.async_get_circle_members, "circle members"),
        update_interval=timedelta(seconds=refresh_interval_seconds),
    )
    try:
        await members_coordinator.async_config_entry_first_refresh()
    except Exception as e:
        _LOGGER.warning("Failed to fetch circle members: %s", e)
    circle_members = members_coordinator.data or []

    # The rewards catalog is readable by any circle member, but redemptions are
    # admin-only and answer 403 otherwise. Treat that as a permission state: keep
    # the catalog, drop the redemptions, and say so once rather than every refresh.
    redemptions_forbidden = False

    async def async_update_rewards() -> dict:
        nonlocal redemptions_forbidden
        rewards = await client.async_get_rewards()
        try:
            redemptions = await client.async_get_redemptions()
        except aiohttp.ClientResponseError as e:
            if e.status != 403:
                raise
            if not redemptions_forbidden:
                redemptions_forbidden = True
                _LOGGER.warning(
                    "GSD token is not a circle admin, so reward redemptions are "
                    "unavailable; the rewards catalog will still be reported"
                )
            return {"rewards": rewards, "redemptions": [], "redemptions_available": False}

        redemptions_forbidden = False
        return {"rewards": rewards, "redemptions": redemptions, "redemptions_available": True}

    rewards_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="gsd_rewards",
        update_method=_coordinator_update(async_update_rewards, "rewards"),
        update_interval=timedelta(seconds=refresh_interval_seconds),
    )
    try:
        await rewards_coordinator.async_config_entry_first_refresh()
    except Exception as e:
        _LOGGER.warning("Failed to fetch GSD rewards: %s", e)

    checkpoints_coordinator = _create_checkpoints_coordinator(hass, client)
    try:
        await checkpoints_coordinator.async_config_entry_first_refresh()
    except Exception as e:
        _LOGGER.warning("Failed to fetch GSD checkpoints: %s", e)

    hass.data[DOMAIN][entry.entry_id] = {
        CONF_URL: entry.data[CONF_URL],
        CONF_TOKEN: entry.data[CONF_TOKEN],
        CONF_SHOW_DUE_IN: entry.data.get(CONF_SHOW_DUE_IN, 7),
        "coordinator": coordinator,
        "members_coordinator": members_coordinator,
        "rewards_coordinator": rewards_coordinator,
        "checkpoints_coordinator": checkpoints_coordinator,
        "client": client,
        "circle_members": circle_members,
    }

    # Register services before setting up platforms
    async def complete_task_handler(call: ServiceCall) -> None:
        await async_complete_task_service(hass, call)

    async def create_task_handler(call: ServiceCall) -> None:
        await async_create_task_service(hass, call)

    async def update_task_handler(call: ServiceCall) -> None:
        await async_update_task_service(hass, call)

    async def delete_task_handler(call: ServiceCall) -> None:
        await async_delete_task_service(hass, call)

    async def skip_task_handler(call: ServiceCall) -> None:
        await async_skip_task_service(hass, call)

    async def approve_chores_handler(call: ServiceCall) -> ServiceResponse:
        return await async_review_chores_service(hass, call, "approve")

    async def reject_chores_handler(call: ServiceCall) -> ServiceResponse:
        return await async_review_chores_service(hass, call, "reject")

    hass.services.async_register(
        DOMAIN, SERVICE_COMPLETE_TASK, complete_task_handler, schema=COMPLETE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_TASK, create_task_handler, schema=CREATE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UPDATE_TASK, update_task_handler, schema=UPDATE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_TASK, delete_task_handler, schema=DELETE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SKIP_TASK, skip_task_handler, schema=SKIP_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPROVE_CHORES,
        approve_chores_handler,
        schema=APPROVE_CHORES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REJECT_CHORES,
        reject_chores_handler,
        schema=REJECT_CHORES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.debug("Registered services: %s", ", ".join(f"{DOMAIN}.{s}" for s in ALL_SERVICES))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.add_update_listener(async_reload_entry)

    return True


def _create_checkpoints_coordinator(
    hass: HomeAssistant, client: GsdApiClient
) -> DataUpdateCoordinator:
    """Coordinator for checkpoints and pending approvals, polled every 30 s.

    Its data is a dict:
      checkpoints            the checkpoints payload, or None if unsupported
      checkpoints_supported  False once the server has answered 404
      pending                list of pending approvals, or None if unsupported
      pending_supported      False on 404 (old server) or 403 (not permitted)

    A 404 is an older server, not a fault: it is logged once at info level and
    the coordinator still succeeds, so nothing goes unavailable for it. Any other
    failure (server unreachable, 5xx) fails the update, which turns every entity
    on this coordinator unavailable.
    """
    logged: set[str] = set()

    async def async_update_checkpoints() -> dict:
        result = {
            "checkpoints": None,
            "checkpoints_supported": True,
            "pending": None,
            "pending_supported": True,
        }

        try:
            result["checkpoints"] = await client.async_get_checkpoints()
            logged.discard("checkpoints")
        except GsdApiError as err:
            if err.status != 404:
                raise
            result["checkpoints_supported"] = False
            if "checkpoints" not in logged:
                logged.add("checkpoints")
                _LOGGER.info(
                    "GSD server has no checkpoints endpoint (HTTP 404); "
                    "checkpoint binary sensors will not be created"
                )

        try:
            result["pending"] = await client.async_get_pending_approvals()
            logged.discard("pending")
        except GsdApiError as err:
            if err.status not in (403, 404):
                raise
            result["pending_supported"] = False
            if "pending" not in logged:
                logged.add("pending")
                _LOGGER.info(
                    "GSD pending approvals are unavailable (HTTP %s); "
                    "the pending approvals sensor will not be created",
                    err.status,
                )

        # Stop polling an old server every 30 seconds; keep checking hourly so
        # a server upgrade is picked up without a restart.
        both_missing = not result["checkpoints_supported"] and not result["pending_supported"]
        coordinator.update_interval = timedelta(
            seconds=CHECKPOINT_UNSUPPORTED_REFRESH_INTERVAL
            if both_missing
            else CHECKPOINT_REFRESH_INTERVAL
        )
        return result

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="gsd_checkpoints",
        update_method=_coordinator_update(async_update_checkpoints, "checkpoints"),
        update_interval=timedelta(seconds=CHECKPOINT_REFRESH_INTERVAL),
    )
    return coordinator


def _entry_config(hass: HomeAssistant, call: ServiceCall) -> dict:
    """Resolve the config entry a service call targets and return its hass.data."""
    entry = _get_config_entry(hass, call.data.get("config_entry_id"))
    config = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if config is None:
        raise HomeAssistantError(f"GSD integration {entry.title} is not loaded")
    return config


async def async_complete_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the complete_task service call."""
    task_id = call.data["task_id"]
    completed_by = call.data.get("completed_by")
    config = _entry_config(hass, call)

    try:
        task = await config["client"].async_complete_task(task_id, completed_by)
    except HomeAssistantError:
        raise
    except Exception as e:
        raise HomeAssistantError(f"Failed to complete GSD task {task_id}: {e}") from e

    if task.status == CHORE_STATUS_PENDING_APPROVAL:
        _LOGGER.info("Task %d submitted for approval by user %s", task_id, completed_by or "default")
    else:
        _LOGGER.info("Task %d completed by user %s", task_id, completed_by or "default")
    await config["coordinator"].async_request_refresh()
    await config["checkpoints_coordinator"].async_request_refresh()


async def async_create_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the create_task service call."""
    name = call.data["name"]
    config = _entry_config(hass, call)

    try:
        result = await config["client"].async_create_task(
            name,
            call.data.get("description"),
            call.data.get("due_date"),
            call.data.get("created_by"),
        )
    except HomeAssistantError:
        raise
    except Exception as e:
        raise HomeAssistantError(f"Failed to create GSD task '{name}': {e}") from e

    _LOGGER.info("Task '%s' created with ID %d", name, result.id)
    await config["coordinator"].async_request_refresh()


async def async_update_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the update_task service call."""
    task_id = call.data["task_id"]
    config = _entry_config(hass, call)

    try:
        await config["client"].async_update_task(
            task_id,
            call.data.get("name"),
            call.data.get("description"),
            call.data.get("due_date"),
        )
    except HomeAssistantError:
        raise
    except Exception as e:
        raise HomeAssistantError(f"Failed to update GSD task {task_id}: {e}") from e

    _LOGGER.info("Task %d updated", task_id)
    await config["coordinator"].async_request_refresh()


async def async_delete_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the delete_task service call."""
    task_id = call.data["task_id"]
    config = _entry_config(hass, call)

    try:
        await config["client"].async_delete_task(task_id)
    except HomeAssistantError:
        raise
    except Exception as e:
        raise HomeAssistantError(f"Failed to delete GSD task {task_id}: {e}") from e

    _LOGGER.info("Task %d deleted", task_id)
    await config["coordinator"].async_request_refresh()


async def async_skip_task_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the skip_task service call."""
    task_id = call.data["task_id"]
    config = _entry_config(hass, call)

    try:
        await config["client"].async_skip_task(task_id, call.data.get("completed_by"))
    except HomeAssistantError:
        raise
    except Exception as e:
        raise HomeAssistantError(f"Failed to skip GSD task {task_id}: {e}") from e

    _LOGGER.info("Task %d skipped", task_id)
    await config["coordinator"].async_request_refresh()


async def async_review_chores_service(
    hass: HomeAssistant, call: ServiceCall, action: str
) -> ServiceResponse:
    """Handle approve_chores / reject_chores.

    Returns {"done": [...], "failed": [{"choreId", "error"}, ...]} as the service
    response. A partial failure is logged and returned; if nothing at all went
    through, the call raises so the caller sees the server's reasons.
    """
    chore_ids = call.data.get("chore_ids") or []
    approve_all = action == "approve" and call.data.get("all", False)
    if not chore_ids and not approve_all:
        if action == "approve":
            raise HomeAssistantError("Give chore_ids, or set all to true to approve every pending chore")
        raise HomeAssistantError("Give the chore_ids to reject")

    config = _entry_config(hass, call)
    try:
        result = await config["client"].async_bulk_review(
            action,
            chore_ids=chore_ids,
            approve_all=approve_all,
            note=call.data.get("note") if action == "reject" else None,
        )
    except HomeAssistantError:
        raise
    except Exception as e:
        raise HomeAssistantError(f"Failed to {action} GSD chores: {e}") from e

    # Refresh even after a partial failure: whatever did go through changed state.
    await config["checkpoints_coordinator"].async_request_refresh()
    await config["coordinator"].async_request_refresh()

    done, failed = result["done"], result["failed"]
    verb = "approved" if action == "approve" else "rejected"
    if failed:
        _LOGGER.warning("GSD %s %d chore(s); %d failed: %s", verb, len(done), len(failed), failed)
        if not done:
            reasons = "; ".join(
                f"{f.get('choreId')}: {f.get('error')}" if isinstance(f, dict) else str(f)
                for f in failed
            )
            raise HomeAssistantError(f"No GSD chores were {verb}: {reasons}")
    else:
        _LOGGER.info("GSD %s chore(s): %s", verb, done)

    return {"done": done, "failed": failed}


def _get_config_entry(hass: HomeAssistant, config_entry_id: str = None) -> ConfigEntry:
    """Get the config entry to use for a service call. Raises if there is none."""
    if config_entry_id:
        entry = hass.config_entries.async_get_entry(config_entry_id)

        # Not an entry ID: accept a GSD entity ID and use the entry that owns it.
        if not entry and "." in config_entry_id:
            entity_entry = er.async_get(hass).async_get(config_entry_id)
            if entity_entry and entity_entry.config_entry_id:
                entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)

        if not entry or entry.domain != DOMAIN:
            raise HomeAssistantError(f"GSD config entry not found for: {config_entry_id}")
        return entry

    # Use the first GSD integration if no specific entry provided
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No GSD integration is configured")
    return entries[0]


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

        # Remove services if this is the last config entry
        if not hass.data[DOMAIN]:
            for service_name in ALL_SERVICES:
                if hass.services.has_service(DOMAIN, service_name):
                    hass.services.async_remove(DOMAIN, service_name)
            _LOGGER.debug("Removed GSD services")

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
