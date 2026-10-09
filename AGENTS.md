# Agent notes

Home Assistant custom integration for GSD. Python, no build step, no test suite.

## Layout

```
custom_components/gsd/
  __init__.py        config entry setup, coordinators, hass.data wiring
  api.py             every HTTP call to the GSD server
  model.py           dataclasses parsed from server JSON
  const.py           DOMAIN and config keys
  config_flow.py     setup + options dialogs
  sensor.py          sensor platform entry point — dispatches to the sensor modules
  chore_sensor.py    one sensor per active chore
  member_sensor.py   points balance per circle member
  reward_sensor.py   rewards catalogue + pending redemptions
  todo.py            unified and per-assignee todo lists
  calendar.py        chore calendar + activity log
  thing.py           GSD "things" — backs switch.py / number.py / text.py
```

`sensor.py` is the only entry point Home Assistant calls for the sensor platform. A new
sensor module does nothing until `sensor.py` imports and calls it — that's how points and
rewards sat dead for months.

## Don't change these

The server is GSD, a Donetick fork, and still speaks Donetick's wire format. The rebrand
stopped at the Home Assistant boundary.

- **Route paths** — `/eapi/v1/chore`, `/eapi/v1/circle/members`, `/eapi/v1/things`,
  `/eapi/v1/rewards`, `/api/v1/chores/*`. None contains the word "donetick".
- **The `secretkey` auth header.** Not `Authorization`.
- **Server JSON keys** — `assignedTo`, `pointsRedeemed`, `nextDueDate`, `isActive`,
  `rewardName`, `displayName`, `frequencyType`, `redeemedAt`. These are what GSD emits.
  Only attribute keys this integration *publishes* to Home Assistant carry the GSD name.
- **Config entry data keys** in `const.py` — `url`, `token`, `show_due_in`,
  `create_unified_list`, `create_assignee_lists`, `refresh_interval`. Changing these
  orphans existing config entries.

Server-side routes live in the GSD repo at `D:\Code\gsd`, under `internal/*/api.go`.
Grep for `eapi` to find them.

## Deploying

The live instance reads from `\\homeassistant\config\custom_components\gsd\`. Copy the
tree there and restart Home Assistant — Python in `custom_components` only loads at
startup, so nothing takes effect until the restart finishes.

Deleting `__pycache__` on the share is worth doing when a module is renamed or removed;
Home Assistant will happily import a stale `.pyc` for a file that no longer exists.

## Entity registry gotchas

Entities are keyed on `unique_id`, and Home Assistant restores registry entries for
unique_ids nothing claims any more. They come back `unavailable` forever and no error is
logged — the integration isn't failing, it just never creates them.

So an `unavailable` entity means one of three things, and they look identical:

1. The code that creates it is never called.
2. Its `unique_id` scheme changed and the old entry is stranded.
3. The thing it represents was deleted server-side while HA was down.

Check which before assuming a server bug. `member_sensor.py` and `reward_sensor.py` were
case 1; the stranded per-user entities for a deleted circle member were case 2.

Changing a `unique_id` format orphans every existing entity in that family. Changing
`DOMAIN` orphans all of them.

## Verifying a change

```bash
python -m compileall -q custom_components/gsd
python tests/test_member_sensor.py
```

`tests/` stubs the handful of `homeassistant` modules a file imports and drives the real
code, so logic can be checked without HA, without dependencies, and without a restart.
`test_member_sensor.py` is the worked example — copy its stub block for anything else.
Note the package's `__init__.py` imports aiohttp, so tests register a bare `gsd` package
pointing at the source directory rather than importing it normally.

Then, after deploying and restarting, in **Developer Tools → Template**:

```jinja
{{ integration_entities('gsd') | count }}
{{ integration_entities('gsd') | select('is_state', 'unavailable') | list }}
```

Every entity should be claimed and none unavailable. `Settings → System → Logs`, filtered
to `custom_components.gsd`, catches the rest — but note that silently-unavailable entities
log nothing at all.
