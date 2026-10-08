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
| `todo` | An "All Tasks" list plus one list per circle member |
| `calendar` | Chore schedule and activity log |
| `switch` / `number` / `text` | GSD "things", typed by their state |

Chore sensors appear and disappear as chores are created and deleted. Deleting a chore
in GSD removes its entity on the next poll, and stale entities left behind by a chore
deleted while Home Assistant was stopped get cleaned up at startup.

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
```

Upstream Donetick does not serve the rewards routes — they're a GSD addition. Point this
at stock Donetick and the rewards sensors will be unavailable. Everything else works.

## Renaming from the Donetick integration

The domain changed, so Home Assistant treats this as a different integration entirely.
Remove the old Donetick config entry, add GSD, and re-enter the token. Every entity gets
a new `gsd.*` id and the old `donetick.*` entities become orphans you can delete from the
entity registry. Nothing migrates. Any dashboard or automation referencing the old ids
needs updating.
