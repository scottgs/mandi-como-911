# mandi-como-911

A Home Assistant panel showing Columbia/Boone County 911 dispatch activity:
a "Near Real-time Fire/Medical" tab (clickable Google Maps address links,
nature color-coded by category, an icon in front of each Nature value), a
"Delayed Police" tab (Columbia's police feed is officially ~6h delayed by
city policy, so it's polled less often and shown accordingly; Type column
also gets an icon, no color-coding on this tab), and a "Map" tab
(visualizing the last 12h of Fire/Medical calls as pins on an interactive
map).

Self-contained: this repo owns its own fetch scripts, PostgreSQL/PostGIS
schema, systemd timers, and Home Assistant dashboard/package YAML. It can be
installed onto any MANDI/Home-Assistant host independent of any other panel.

## How it works

Two independent fetch scripts, each on its own systemd timer:

- `fetch/columbia-911-fire-fetch.py` — scrapes como.gov's fire/medical CSV
  export every 5 minutes, transforms each call's Missouri State Plane
  coordinates (ESRI:102697) to WGS84, upserts into `fire_medical_calls`.
- `fetch/columbia-911-police-fetch.py` — scrapes the police CSV export every
  30 minutes (no coordinates in this feed; matches the source's own ~6h
  delay, faster polling has no benefit). Upserts into `police_calls`.

Both then query the last rolling 24h back out of the database and atomically
rewrite a JSON cache under Home Assistant's `www/` directory.
`ha/packages/columbia_911.yaml` exposes both caches as `command_line`
sensors; `ha/lovelace/columbia_911.yaml` renders the two-tab dashboard.

Each fetch script also computes a `nature_icon` per call — a substring
keyword match against the `nature` text (e.g. `medical` → 🚑, `fire` → 🔥,
`welfare` → ❤️, `traffic stop` → 🚓), checked most-specific-first so a
narrow keyword like `carbon monoxide` or `welfare` is tried before a
broader one (`alarm`, `check`) that would otherwise swallow it. The two
scripts keep independent keyword lists — fire/medical's 12 keywords vs.
police's 30 — since the two feeds' `nature` vocabularies barely overlap.
A `nature` with no keyword match gets no icon rather than a guessed one.

`db/schema.sql` carries `COMMENT ON TABLE`/`COMMENT ON COLUMN` documentation
for every column — in particular `geox`/`geoy`'s coordinate reference system
(Missouri State Plane Central Zone, NAD83, US Survey Feet — ESRI:102697) and
how `geom` relates to them (the `pyproj` transform to WGS84/EPSG:4326 done at
fetch time). Visible via `\d+ fire_medical_calls` in `psql`, not just here.

## Map tab

The Map tab displays Fire/Medical dispatch calls from the last 12 hours as pins
on an interactive map. Police calls are excluded because the police feed
contains no coordinates.

The map is implemented as a custom Lovelace card (`ha/www/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js`)
with Leaflet bundled directly (not a HACS dependency), allowing deployment to
any Home Assistant instance without additional package management. Both `install.sh`
and `uninstall.sh` handle the card's deployment and removal, including Lovelace
resource registration in `configuration.yaml`.

The custom card uses a ResizeObserver to ensure the map initializes correctly
when rendered in Home Assistant's dynamically-sized dashboard tabs, preventing
the Leaflet rendering bug where the map would lock onto a zero-sized container.

## Prerequisites

- PostgreSQL reachable as `localhost` **with the PostGIS extension
  available** (`CREATE EXTENSION postgis` must succeed — on Debian/Ubuntu
  that's the `postgresql-*-postgis-3` package). This is the one extra
  dependency this repo has that `mandi-cou-flights` doesn't.
- Home Assistant with `command_line` sensor support (core, no HACS
  dependency).
- Python 3 with `psycopg2` **and `pyproj`** available to the install user
  (`pyproj` is only needed by the fire/medical script, for the coordinate
  transform).

## Install

```
./install.sh <install-user> <repo-dir> <ha-config-dir>
# e.g. on srs9 itself:
./install.sh scottgs /home/scottgs/repos/mandi-como-911 /home/scottgs/homeassistant/config
```

This provisions the `mandi_geo` role + `mandi_geo` database + schema (only
if they don't already exist — `db/schema.sql` is pure `IF NOT EXISTS`, never
destructive), installs and enables both fetch timers, and copies the
dashboard + package YAML into your Home Assistant config. It prints the one
remaining manual step: registering the dashboard in `configuration.yaml`
(see the script's own output for the exact YAML block), since that file
isn't owned by this repo.

First run prompts for `MANDI_GEO_DB_PASSWORD` if `/etc/mandi/como-911.env`
doesn't already exist; see `.env.example`.

## Uninstall

```
./uninstall.sh <ha-config-dir>
```

Removes both systemd timers and the dashboard/package YAML. Deliberately
does **not** drop the database or role — that's printed as a manual step,
since it's the one genuinely destructive action in this whole repo.

## Design history

Original dashboard design spec: `docs/columbia-911-dashboard-plan.md`.
Fire/medical fetch script rationale (coordinate transform, WAF-blocking
gotcha, etc.): `docs/tender-wibbling-treehouse.md`.
