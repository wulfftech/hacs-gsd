# GSD — Home Assistant integration

Surfaces [GSD](https://github.com/wulfftech/gsd) chores, tasks, points and rewards as
Home Assistant entities.

Forked from [donetick/donetick-hass-integration](https://github.com/donetick/donetick-hass-integration)
and rebranded onto the `gsd` domain. Upstream carries no licence — see
[NOTICE.md](NOTICE.md) for what came from where. GSD is itself a Donetick fork, so the wire
format is still Donetick's — the server routes, the `secretkey` header and every JSON
field name are unchanged. Only the Home Assistant side carries the GSD name.

## What you get

| Platform | Entities |
|---|---|
| `sensor` | One per active chore, carrying due date, assignee, frequency, priority and labels |
| `sensor` | Points balance per circle member |
| `sensor` | Rewards catalogue and pending redemptions |
| `sensor` | `sensor.gsd_pending_approvals`: chore completions waiting for approval |
| `binary_sensor` | Checkpoint ("baseline done") per kid and for all kids; see below |
| `todo` | An "All Tasks" list plus one list per circle member |
| `calendar` | Chore schedule and activity log |
| `switch` / `number` / `text` | GSD "things", typed by their state |

Chore sensors appear and disappear as chores are created and deleted. Deleting a chore
in GSD removes its entity on the next poll, and stale entities left behind by a chore
deleted while Home Assistant was stopped get cleaned up at startup.

## Checkpoints and approvals

Kids' jobs can be tagged with a checkpoint (`morning`, `afternoon`, `evening`). These
entities are polled every 30 seconds, whatever the chore refresh interval is set to.
Their ids are fixed so automations can rely on them:

| Entity | On when |
|---|---|
| `binary_sensor.gsd_all_kids_morning_done` (and `_afternoon_`, `_evening_`) | Every kid is `done` or has nothing for that checkpoint |
| `binary_sensor.gsd_<kid>_<checkpoint>_done` | That kid's state is `done` or `none` |
| `sensor.gsd_pending_approvals` | State is the number of completions waiting for approval |

`<kid>` is the kid's name slugified when the entity is first created (`Sam Lee` →
`gsd_sam_lee_morning_done`); renaming the kid later changes the friendly name, not the
id. Kids are circle members with the `member` role.

A kid's sensor carries `state` (`none`, `todo`, `waiting` or `done`), `total`, `approved`,
`pending`, `remaining`, `user_id` and `date`. An all-kids sensor carries `date` and `kids`,
a map of each kid's name to their state. The pending sensor's `items` attribute lists
`choreId`, `name`, `checkpoint`, `completedByName` and `performedAt` for each completion.

All of them go `unavailable`, not off, while the GSD server can't be reached. A server
without the checkpoints endpoint gets none of these entities; that's logged once at info
level.

## Services

| Service | Does |
|---|---|
| `gsd.complete_task` | Complete a chore. One that needs approval is submitted for approval instead, which isn't an error |
| `gsd.create_task` / `gsd.update_task` / `gsd.delete_task` / `gsd.skip_task` | Chore CRUD. `update_task` reads the whole chore and sends it back with your changes, so fields it doesn't touch survive |
| `gsd.approve_chores` | Approve pending completions: `chore_ids: [12, 13]`, or `all: true` |
| `gsd.reject_chores` | Reject pending completions: `chore_ids: [12]`, optional `note` |

Approve and reject need an admin or manager token. Both return `{done: [...], failed:
[{choreId, error}]}` as a service response (`response_variable` in a script). Chores that
fail are logged as a warning; if none went through, the call fails with the server's
reasons. A failed call to any service raises an error carrying the server's message
instead of only logging it.

## Install

Copy `custom_components/gsd/` into your Home Assistant `custom_components/` directory
and restart. Then **Settings → Devices & Services → Add Integration → GSD**.

You need the GSD base URL and an API token. Generate the token in GSD under
**Settings → API tokens**.

| Option | Default | Notes |
|---|---|---|
| URL | — | e.g. `https://gsd.example.com/` |
| API token | — | Sent as the `secretkey` header |
| Refresh interval | 900s | 15 minutes |
| Days ahead to show | 7 | Horizon for upcoming tasks |
| Create "All Tasks" list | on | Single unified todo list |
| Create per-person lists | off | One todo list per circle member |

**The token's account must be a circle admin**, or the pending-redemptions sensor gets a
403 from `GET /eapi/v1/rewards/redemptions` and goes unavailable. The rewards catalogue
has no such requirement.

## Server requirements

Points and rewards need a GSD build that serves these routes:

```
GET /eapi/v1/circle/members        points + pointsRedeemed per member
GET /eapi/v1/rewards               rewards catalogue
GET /eapi/v1/rewards/redemptions   pending redemptions (admin only)
GET /eapi/v1/household/checkpoints checkpoint binary sensors
GET /eapi/v1/chore/pending         pending approvals sensor
POST /eapi/v1/chore/approve        gsd.approve_chores
POST /eapi/v1/chore/reject         gsd.reject_chores
```

Upstream Donetick does not serve the rewards routes — they're a GSD addition. Point this
at stock Donetick and the rewards sensors will be unavailable. Everything else works.

## Renaming from the Donetick integration

The domain changed, so Home Assistant treats this as a different integration entirely.
Remove the old Donetick config entry, add GSD, and re-enter the token. Every entity gets
a new `gsd.*` id and the old `donetick.*` entities become orphans you can delete from the
entity registry. Nothing migrates. Any dashboard or automation referencing the old ids
needs updating.
