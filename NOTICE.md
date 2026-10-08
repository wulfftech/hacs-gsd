# Provenance and licensing

This repository is a fork of
[donetick/donetick-hass-integration](https://github.com/donetick/donetick-hass-integration),
rebranded from the `donetick` domain to `gsd` and extended.

## Upstream carries no licence

The upstream repository has no LICENSE file, and the GitHub API reports its licence as
`null`. No terms of redistribution or modification have been published for it. Under
default copyright, rights are reserved to its authors.

Roughly 93% of the Python here is upstream code, unmodified except for the rebrand:
2,565 of 2,749 lines as of the import commit. It is reproduced here without a licence
granting that right. If you are an upstream author and want this taken down or
relicensed, open an issue and it will be actioned.

Upstream codeowner: **@meauxt**. Contributors to the files reproduced here include
Carlos Urreta and Torben Van Assche, who wrote `calendar.py` and `chore_sensor.py`.

## What is original to this fork

Two files, 184 lines, written by the repository owner and present nowhere upstream:

| File | Lines |
|---|---|
| `custom_components/gsd/member_sensor.py` | 74 |
| `custom_components/gsd/reward_sensor.py` | 110 |

Plus the rebrand, the wiring that makes those two modules run, the rewards API client,
and the fixes recorded in the commit history.

## Why no LICENSE file

Applying a licence to this tree would mean granting rights over upstream code that
nobody has granted to us. So there isn't one. The original 184 lines above are offered
under the MIT licence; everything else remains under its authors' reserved rights.

## Related

The GSD server this talks to is [wulfftech/gsd](https://github.com/wulfftech/gsd), a
fork of [donetick/donetick](https://github.com/donetick/donetick). That one *is*
licensed — AGPL-3.0 — and the fork is made under those terms.
