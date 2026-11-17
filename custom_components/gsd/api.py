"""API client for GSD."""
import asyncio
import logging
import json
from typing import Any, Callable, List, Optional
import aiohttp
from homeassistant.exceptions import HomeAssistantError

from .const import API_TIMEOUT
from .model import (
    GsdTask,
    GsdThing,
    GsdMember,
    GsdChoreHistory,
    GsdReward,
    GsdRewardRedemption,
)
_LOGGER = logging.getLogger(__name__)

# Chore.status value the server sets when a completion is waiting for approval.
CHORE_STATUS_PENDING_APPROVAL = 3


class GsdApiError(HomeAssistantError):
    """A GSD request failed. Carries the HTTP status and the server's message.

    Subclasses HomeAssistantError so a service handler can let it propagate and
    Home Assistant shows the server's own `error` text to whoever called it.
    """

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def _unwrap(data: Any) -> Any:
    """Return the payload of a response.

    Newer GSD routes wrap their payload as {"res": ...}; the original eapi routes
    return it bare. Accept both so neither a server upgrade nor an old server
    breaks parsing. A chore object never has a top-level "res" key.
    """
    if isinstance(data, dict) and "res" in data:
        return data["res"]
    return data


class GsdApiClient:
    """API client for GSD."""

    def __init__(self, base_url: str, token: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self._base_url = base_url.rstrip('/')
        self._token = token
        self._session = session

    def _headers(self) -> dict:
        """Return headers for GSD API requests."""
        return {
            "secretkey": f"{self._token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        payload: Any = None,
    ) -> Any:
        """Send a request and return the decoded JSON body (None if empty).

        Raises GsdApiError for a transport failure, a timeout, or a 4xx/5xx
        answer; in the last case the message is the server's `error` field.
        """
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as response:
                text = await response.text()
                body: Any = None
                if text:
                    try:
                        body = json.loads(text)
                    except ValueError:
                        body = None

                if response.status >= 400:
                    message = None
                    if isinstance(body, dict):
                        message = body.get("error") or body.get("message")
                    if not message:
                        message = text.strip()[:200] or response.reason or "request failed"
                    raise GsdApiError(
                        f"GSD {method} {path} failed (HTTP {response.status}): {message}",
                        response.status,
                    )

                if text and body is None:
                    raise GsdApiError(f"GSD {method} {path} returned a non-JSON response")
                return body

        except asyncio.TimeoutError as err:
            raise GsdApiError(f"GSD {method} {path} timed out") from err
        except aiohttp.ClientError as err:
            raise GsdApiError(f"Cannot reach GSD ({method} {path}): {err}") from err

    async def _get_list(self, path: str, parse: Callable[[dict], Any], what: str) -> list:
        """GET a list endpoint and parse each row.

        Uses raise_for_status so callers that branch on aiohttp's
        ClientResponseError (the redemptions 403) keep working. A malformed
        response raises instead of quietly returning [], so the coordinator
        marks its entities unavailable rather than reporting "nothing".
        """
        async with self._session.get(
            f"{self._base_url}{path}",
            headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
        ) as response:
            response.raise_for_status()
            try:
                data = _unwrap(await response.json())
            except (ValueError, aiohttp.ContentTypeError) as err:
                raise GsdApiError(f"GSD {what} response was not JSON: {err}") from err

        if not isinstance(data, list):
            raise GsdApiError(f"Unexpected response format from GSD {what} API")
        try:
            return [parse(row) for row in data]
        except (KeyError, TypeError, ValueError) as err:
            raise GsdApiError(f"Error parsing GSD {what} response: {err!r}") from err

    async def async_get_tasks(self) -> List[GsdTask]:
        """Get tasks from GSD."""
        return await self._get_list("/eapi/v1/chore", GsdTask.from_json, "chores")

    async def async_get_circle_members(self) -> List[GsdMember]:
        """Get circle members from GSD."""
        return await self._get_list(
            "/eapi/v1/circle/members", GsdMember.from_json, "circle members"
        )

    async def async_get_rewards(self) -> List[GsdReward]:
        """Get the reward catalog from GSD."""
        return await self._get_list("/eapi/v1/rewards", GsdReward.from_json, "rewards")

    async def async_get_redemptions(self) -> List[GsdRewardRedemption]:
        """Get reward redemptions from GSD. Requires a circle admin token."""
        try:
            return await self._get_list(
                "/eapi/v1/rewards/redemptions",
                GsdRewardRedemption.from_json,
                "redemptions",
            )
        except aiohttp.ClientResponseError as err:
            # The server answers 403 unless the token's user is a circle admin. That
            # is a permission state rather than a fault, so raise it without the
            # error-level noise and let the caller decide how to carry on.
            if err.status == 403:
                _LOGGER.debug("GSD redemptions need a circle admin token: HTTP 403")
            raise

    async def async_get_things(self) -> List[GsdThing]:
        """Get things from GSD."""
        return await self._get_list("/eapi/v1/things", GsdThing.from_json, "things")

    async def async_get_thing_state(self, thing_id: int) -> Optional[str]:
        """Get the current state of a thing."""
        headers = self._headers()
        
        try:
            async with self._session.get(
                f"{self._base_url}/eapi/v1/things/{thing_id}",
                headers=headers,
                timeout=API_TIMEOUT
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("thing", {}).get("state")
                
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching thing state from GSD: %s", err)
            raise
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.error("Error parsing GSD thing state response: %s", err)
            return None

    async def async_set_thing_state(self, thing_id: int, state: str) -> bool:
        """Set the state of a thing directly."""
        headers = self._headers()
        
        params = {"state": state}
        
        try:
            async with self._session.get(
                f"{self._base_url}/eapi/v1/things/{thing_id}/state",
                headers=headers,
                params=params,
                timeout=API_TIMEOUT
            ) as response:
                response.raise_for_status()
                return True
                
        except aiohttp.ClientError as err:
            _LOGGER.error("Error setting thing state in GSD: %s", err)
            raise
        except Exception as err:
            _LOGGER.error("Error setting thing state: %s", err)
            return False

    async def async_change_thing_state(self, thing_id: int, new_state: str = None, increment: int = None) -> Optional[str]:
        """Change the state of a thing using the change endpoint."""
        headers = self._headers()
        
        params = {}
        if new_state is not None:
            params["set"] = new_state
        if increment is not None:
            params["op"] = increment
        
        try:
            async with self._session.get(
                f"{self._base_url}/eapi/v1/things/{thing_id}/state/change",
                headers=headers,
                params=params,
                timeout=API_TIMEOUT
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("state")
                
        except aiohttp.ClientError as err:
            _LOGGER.error("Error changing thing state in GSD: %s", err)
            raise
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.error("Error parsing GSD change state response: %s", err)
            return None

    async def async_complete_task(self, choreId: int, completed_by: int = None) -> GsdTask:
        """Complete a task.

        A chore that needs approval is not completed outright: the server parks it
        as pending and answers {"res": <chore>, "message": "...submitted for
        approval"} instead of the bare chore. That is a success, so unwrap it and
        hand back the chore (its status is CHORE_STATUS_PENDING_APPROVAL).
        """
        params = {}
        if completed_by:
            params["completedBy"] = completed_by

        data = await self._request(
            "POST", f"/eapi/v1/chore/{choreId}/complete", params=params
        )
        message = data.get("message") if isinstance(data, dict) else None
        chore = _unwrap(data)
        if not isinstance(chore, dict):
            raise GsdApiError(f"Unexpected response completing GSD chore {choreId}")

        task = GsdTask.from_json(chore)
        if message or task.status == CHORE_STATUS_PENDING_APPROVAL:
            _LOGGER.info(
                "GSD chore %d submitted for approval%s",
                choreId,
                f": {message}" if message else "",
            )
        return task

    async def async_create_task(self, name: str, description: str = None, due_date: str = None, created_by: int = None) -> GsdTask:
        """Create a new task"""
        payload = {"name": name}
        if description:
            payload["description"] = description
        if due_date:
            payload["dueDate"] = due_date
        if created_by:
            payload["createdBy"] = created_by

        data = _unwrap(await self._request("POST", "/eapi/v1/chore", payload=payload))
        if not isinstance(data, dict):
            raise GsdApiError("Unexpected response creating GSD chore")
        return GsdTask.from_json(data)

    async def async_get_task_raw(self, task_id: int) -> dict:
        """Return a chore exactly as the server sends it, every field intact.

        Prefers GET /eapi/v1/chore/:id. A server that predates that route answers
        404 or 405, so fall back to picking the chore out of the list endpoint,
        which carries the same full objects.
        """
        try:
            data = _unwrap(await self._request("GET", f"/eapi/v1/chore/{task_id}"))
            if isinstance(data, dict):
                return data
        except GsdApiError as err:
            if err.status not in (404, 405):
                raise

        data = _unwrap(await self._request("GET", "/eapi/v1/chore"))
        if isinstance(data, list):
            for chore in data:
                if isinstance(chore, dict) and chore.get("id") == task_id:
                    return chore
        raise GsdApiError(f"GSD chore {task_id} not found", 404)

    async def async_update_task(self, task_id: int, name: str = None, description: str = None, due_date: str = None) -> GsdTask:
        """Update an existing task.

        PUT /eapi/v1/chore/:id overwrites the chore with what it is sent, so a
        partial body nulls every field left out (description, due date, and on
        newer servers checkpoint, skipHolidays, rotationOrder, rotationAnchor...).
        Fetch the whole chore, change only the fields asked for, and send it all
        back.
        """
        if not name and description is None and not due_date:
            raise ValueError("At least one field must be provided for update")

        chore = dict(await self.async_get_task_raw(task_id))
        if name:
            chore["name"] = name
        if description is not None:  # Allow empty string to clear description
            chore["description"] = description
        if due_date:
            chore["nextDueDate"] = due_date
        # The eapi edit request reads the due date from `dueDate`, not
        # `nextDueDate`; an empty one clears it. Always carry it.
        chore["dueDate"] = chore.get("nextDueDate") or ""

        data = _unwrap(
            await self._request("PUT", f"/eapi/v1/chore/{task_id}", payload=chore)
        )
        if not isinstance(data, dict):
            raise GsdApiError(f"Unexpected response updating GSD chore {task_id}")
        return GsdTask.from_json(data)

    async def async_skip_task(self, choreId: int, completed_by: int = None) -> GsdTask:
        """Skip a task, advancing to the next scheduled occurrence without recording a completion."""
        params = {}
        if completed_by:
            params["completedBy"] = completed_by

        data = _unwrap(
            await self._request("POST", f"/eapi/v1/chore/{choreId}/skip", params=params)
        )
        if not isinstance(data, dict):
            raise GsdApiError(f"Unexpected response skipping GSD chore {choreId}")
        return GsdTask.from_json(data)

    async def async_delete_task(self, task_id: int) -> bool:
        """Delete a task. Raises GsdApiError on failure."""
        await self._request("DELETE", f"/eapi/v1/chore/{task_id}")
        return True

    async def async_get_checkpoints(self) -> dict:
        """GET /eapi/v1/household/checkpoints. A 404 means the server predates it."""
        data = _unwrap(await self._request("GET", "/eapi/v1/household/checkpoints"))
        if not isinstance(data, dict):
            raise GsdApiError("Unexpected response format from GSD checkpoints API")
        return data

    async def async_get_pending_approvals(self) -> list:
        """GET /eapi/v1/chore/pending: completions waiting for approval."""
        data = _unwrap(await self._request("GET", "/eapi/v1/chore/pending"))
        if data is None:
            return []
        if not isinstance(data, list):
            raise GsdApiError("Unexpected response format from GSD pending approvals API")
        return data

    async def async_bulk_review(
        self,
        action: str,
        chore_ids: Optional[List[int]] = None,
        approve_all: bool = False,
        note: Optional[str] = None,
    ) -> dict:
        """POST /eapi/v1/chore/approve or /reject. Returns {"done": [...], "failed": [...]}."""
        if action not in ("approve", "reject"):
            raise ValueError(f"Unknown review action: {action}")

        payload: dict = {}
        if approve_all:
            payload["all"] = True
        else:
            payload["choreIds"] = list(chore_ids or [])
        if note:
            payload["note"] = note

        data = _unwrap(
            await self._request("POST", f"/eapi/v1/chore/{action}", payload=payload)
        )
        if not isinstance(data, dict):
            data = {}
        return {
            "done": list(data.get("done") or []),
            "failed": list(data.get("failed") or []),
        }

    async def async_get_task_detail(self, task_id: int) -> Optional[GsdTask]:
        """Get detailed task timing data when the full API endpoint is available."""
        try:
            async with self._session.get(
                f"{self._base_url}/api/v1/chores/{task_id}/details",
                headers=self._headers(),
                timeout=API_TIMEOUT
            ) as response:
                if response.status in (401, 403, 404):
                    _LOGGER.debug("GSD task detail endpoint unavailable: HTTP %s", response.status)
                    return None

                response.raise_for_status()
                data = await response.json()
                detail = data.get("res", data) if isinstance(data, dict) else data

                if not isinstance(detail, dict):
                    _LOGGER.debug("Unexpected GSD task detail response format")
                    return None

                return GsdTask.from_json(detail)

        except aiohttp.ClientError as err:
            _LOGGER.debug("Unable to fetch GSD task detail %d: %s", task_id, err)
            return None
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.debug("Unable to parse GSD task detail %d: %s", task_id, err)
            return None

    async def async_get_task_history(self, days: int = 30, include_members: bool = True) -> List[GsdChoreHistory]:
        """Get recent chore history when the full API endpoint is available."""
        params = {
            "limit": max(days, 1),
            "members": "true" if include_members else "false",
        }

        try:
            async with self._session.get(
                f"{self._base_url}/api/v1/chores/history",
                headers=self._headers(),
                params=params,
                timeout=API_TIMEOUT
            ) as response:
                if response.status in (401, 403, 404):
                    _LOGGER.debug("GSD task history endpoint unavailable: HTTP %s", response.status)
                    return []

                response.raise_for_status()
                data = await response.json()
                history = data.get("res", data) if isinstance(data, dict) else data

                if not isinstance(history, list):
                    _LOGGER.debug("Unexpected GSD task history response format")
                    return []

                return GsdChoreHistory.from_json_list(history)

        except aiohttp.ClientError as err:
            _LOGGER.debug("Unable to fetch GSD task history: %s", err)
            return []
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.debug("Unable to parse GSD task history: %s", err)
            return []
