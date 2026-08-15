# Columbia 911 Panel — Phase 1: PostgreSQL/PostGIS + Fire/Medical Ingestion

## Context

`~/columbia-911-dashboard-plan.md` is the original design spec for a MANDI
panel showing Columbia/Boone County 911 activity. It assumed HTML-table
scraping and a SQLite store (mirroring the COU Flights panel's
architecture). Grant has revised that in a few rounds of feedback during
this planning session:

- Start with the fire/medical feed only (police comes later), on a 5-minute
  cycle matching the original spec's near-real-time rationale.
- Back it with real **PostgreSQL + PostGIS**, not SQLite — this data is
  genuinely spatial (per-call coordinates, future proximity/heatmap-style
  queries) in a way COU Flights never was.
- **Install PostgreSQL natively on server_name** (not in a Docker container, unlike
  every other service on this box) — latest stable major, PostGIS added as
  its own separate install step on top.
- **Allow LAN connections**, not localhost-only — this instance is meant to
  serve as general-purpose geospatial analytics infrastructure Grant can
  reach from other machines, not just a private store for one panel's
  scraper.
- **Separate tables for fire/medical and police**, not one shared
  agency-agnostic table.
- **Store both the original Missouri State Plane coordinates and the
  transformed EPSG:4326 coordinates** — not transform-and-discard.

This phase's research (done directly against the live site) also settled
some open questions from the original spec:

- **There's a CSV export**, `fire_csvexport.php` — no HTML-table scraping
  needed. Columns: `InNum,CallDateTime,Address,AptLot,geox,geoy,
  ExtNatureDisplayName,Report,PolArea,DOW,Hour,Agency`. `InNum` (e.g.
  `2026221493`) is a sequential dispatch/CAD ID — a clean natural key for
  upsert/dedup, the same role `flight_number`+`scheduled_date` play for COU
  Flights.
- **`geox`/`geoy` are Missouri State Plane Central Zone coordinates** (NAD83,
  US Survey Feet — `ESRI:102697`, Transverse Mercator, central meridian
  -92.5°, latitude of origin 35.8333°). Confirmed by value range (geox
  ~1.6-1.75M, geoy ~1.05-1.2M, the expected magnitude for this zone) and by
  it being the standard system Missouri local-government GIS data is
  published in — Boone County/Columbia falls inside the Central zone.
- **The police feed (`police_csvexport.php`) has no coordinates at all** —
  columns are `InNum,CallDateTime,Address,ExtNatureDisplayName,Report,
  PolArea,DOW,Hour`, address-text only. Mapping it later will need a
  separate geocoding step; out of scope for this phase, but the schema
  below accounts for it (no geo columns on the police table).
- **Playwright is blocked by como.gov's WAF** (an "Attack ID" bot-signature
  block; `curl` with a normal browser UA gets a clean 200 from the same IP).
  All research here was done via `curl`. Worth remembering for any future
  live-verification work against this site — don't hammer it with an
  automated browser, it'll get blocked.
- The site's default view for `fire.php`/its CSV export appears to always
  return "today so far" (confirmed: 35 records at time of testing, page HTML
  said "35 records found, displaying page 1 of 2"); passing
  `Start_Date`/`End_Date` to the CSV export didn't change the result, so
  there's no broader backfill available this way — each poll only ever sees
  today's calls up to now, which is fine since every poll upserts.
- server_name's LAN is `192.168.68.0/22` (interface `enp5s0`, address
  `192.168.68.101`). The host firewall is currently open (`ufw` inactive;
  the live `nftables` ruleset's `INPUT` chain policy is `accept`, with
  Tailscale-specific rules layered on top) — so no firewall changes are
  needed to make a LAN-listening Postgres reachable; this is purely a
  Postgres-side (`postgresql.conf`/`pg_hba.conf`) change.

## What this phase builds

1. **Native PostgreSQL 18 + PostGIS**, installed directly on server_name via `apt`
   (Ubuntu 26.04 "Resolute Raccoon" ships PostgreSQL 18 and
   `postgresql-18-postgis-3` directly in its own archive — no PGDG repo
   needed), configured to accept authenticated connections from the LAN.
2. **A fire/medical ingestion script**, mirroring
   `~/MANDI/cou-flights-fetch.py`'s conventions (host-side Python, `urllib`,
   upsert-by-natural-key, systemd timer) but writing to Postgres/PostGIS.
3. Both the `fire_medical_calls` and `police_calls` tables get created now
   (schema settled), but only the fire/medical fetch script ships this
   phase. No dashboard card yet either — those are next phases, once
   ingestion is proven.

## 1. PostgreSQL + PostGIS install (native)

- **Packages:** `sudo apt install postgresql postgresql-18-postgis-3` —
  pulls PostgreSQL 18.4 (the latest stable major, confirmed already in
  Ubuntu 26.04's own archive) plus PostGIS 3.6 as an explicit second
  package on top of it, matching the "install PostGIS separately into that
  instance" intent from earlier feedback.
- **Data/config locations — already outside the home directory by default**,
  which satisfies the earlier "not in my home directory" ask without any
  custom path: Debian/Ubuntu's native package puts data at
  `/var/lib/postgresql/18/main` and config at `/etc/postgresql/18/main/`
  (`postgresql.conf`, `pg_hba.conf`) — the standard system locations, not
  bind mounts under `~/`.
- **Resource posture — generous, since this is meant to be general-purpose
  geospatial analytics infrastructure**, not container `mem_limit`/`cpus`
  (there's no container now) but `postgresql.conf` tuning, reflecting real
  headroom on server_name (16 logical cores, ~23Gi available of 30Gi total):
  - `shared_buffers = 4GB`
  - `effective_cache_size = 12GB`
  - `work_mem = 64MB`
  - `maintenance_work_mem = 512MB`
  This leaves comfortable headroom for Frigate (currently the heaviest
  consumer) and can be raised further once real analytics workloads are
  running.
- **LAN access:**
  - `postgresql.conf`: `listen_addresses = '*'` (bind all interfaces; actual
    access is controlled by `pg_hba.conf`, not the bind address).
  - `pg_hba.conf`: add `host  all  all  192.168.68.0/22  scram-sha-256` —
    scoped to the actual LAN subnet only, not Tailscale's `100.64.0.0/10`
    range or the `docker0` bridge's `172.17.0.0/16` — so this doesn't
    accidentally also expose the DB over Tailscale or to containers on this
    host. Keep the existing localhost entries as-is.
  - `password_encryption = scram-sha-256` (Postgres 18 default) — no `trust`
    auth for the LAN-facing rule.
  - Restart `postgresql` after config changes.
- **Role/database:** a dedicated non-superuser role (not connecting as
  `postgres`) owning a new database — naming it `mandi_geo` rather than
  `columbia911`, since Grant's intent is broader than just this one panel.
  Password generated and recorded in `~/MANDI/credentials.md`, matching how
  other local secrets on this stack are already tracked.
- **Extension:** `CREATE EXTENSION postgis;` run once inside `mandi_geo`
  after the role/database are created.
- **Worth flagging explicitly:** this opens TCP 5432 to the entire home LAN
  with password auth — a real, if modest, change to this host's network
  exposure (everything else on server_name either binds to `localhost` or is
  Tailscale-only). Reasonable given the stated intent (LAN-reachable
  analytics DB), just flagging it as a deliberate tradeoff, not a default.

## 2. Database schema (`mandi_geo` database)

Two separate tables, as requested — no shared agency-agnostic table. Both
raw state-plane and transformed WGS84 coordinates are stored on the
fire/medical table (police has no coordinates at all in the source feed).

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE fire_medical_calls (
    in_num          BIGINT PRIMARY KEY,       -- CAD dispatch ID, e.g. 2026221493
    source_agency   TEXT,                     -- raw 'Agency' column, e.g. 'CFD ', 'BCFD'
    call_datetime   TIMESTAMPTZ NOT NULL,
    address         TEXT,
    apt_lot         TEXT,
    nature          TEXT,                     -- ExtNatureDisplayName
    report_id       TEXT,
    patrol_area     TEXT,
    geox            NUMERIC,                  -- raw Missouri State Plane Central easting (NAD83, US ft)
    geoy            NUMERIC,                  -- raw Missouri State Plane Central northing (NAD83, US ft)
    geom            GEOGRAPHY(Point, 4326),    -- geox/geoy transformed to WGS84
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX fire_medical_calls_call_datetime_idx ON fire_medical_calls (call_datetime DESC);
CREATE INDEX fire_medical_calls_geom_idx ON fire_medical_calls USING GIST (geom);

CREATE TABLE police_calls (
    in_num          BIGINT PRIMARY KEY,
    call_datetime   TIMESTAMPTZ NOT NULL,
    address         TEXT,
    nature          TEXT,
    report_id       TEXT,
    patrol_area     TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    -- No geox/geoy/geom columns: police_csvexport.php publishes no
    -- coordinates at all (confirmed this session). Add them if a future
    -- phase adds address-based geocoding.
);
CREATE INDEX police_calls_call_datetime_idx ON police_calls (call_datetime DESC);
```

- `geom` stored as `geography` (not bare `geometry`) so distance/radius
  queries work directly in real-world meters.
- `geox`/`geoy` kept as raw `NUMERIC` alongside `geom` per Grant's explicit
  ask — preserves the original published values for provenance/debugging
  and in case the transform ever needs redoing (e.g. SRID correction),
  without needing to re-fetch history.
- `in_num` as primary key gives upsert-on-conflict for free (`ON CONFLICT
  (in_num) DO UPDATE ...`).
- `police_calls` exists now (schema settled per this round's feedback) but
  stays empty until the police ingestion script is built in a later phase.

## 3. Ingestion script

- **New file `~/MANDI/columbia-911-fire-fetch.py`**, following
  `cou-flights-fetch.py`'s shape: stdlib `urllib` GET against
  `fire_csvexport.php`, `csv` module to parse (no BeautifulSoup needed —
  cleaner than the flights scraper, which had to parse HTML).
- **New host dependencies** (via `apt`, not pip/venv — both packaged for
  this Ubuntu release): `python3-psycopg2` (DB writes) and `python3-pyproj`
  (state-plane → WGS84 transform). A deliberate small departure from the
  "stdlib-only" COU Flights convention — that constraint existed
  specifically to avoid dependency drift inside the HA *container image*
  (replaced wholesale on every HA update); this script runs host-side like
  the flights fetcher already does, so apt-installed system packages are
  stable and appropriate.
- **Transform:** `pyproj.Transformer.from_crs("ESRI:102697", "EPSG:4326")`
  once at module load; each row writes both the raw `geox`/`geoy` and the
  transformed `geom`.
- **Upsert:** one `INSERT ... ON CONFLICT (in_num) DO UPDATE` per row
  against `fire_medical_calls`.
- **Connection:** connects to Postgres over `localhost` (the script runs on
  server_name itself — no reason to route its own traffic through the LAN-facing
  listener) using the dedicated role's credentials.
- **Systemd timer:** `columbia-911-fire-fetch.timer` / `.service`, modeled
  directly on the existing
  `/etc/systemd/system/cou-flights-fetch.{timer,service}` units, **5-minute
  interval** (matches the original spec's near-real-time rationale). Police,
  when built later, gets its own timer at a 30-minute cadence (matches its
  6-hour-delayed nature — polling faster than that has no benefit).

## Explicitly out of scope this phase

- Police feed ingestion script (table exists, fetch script doesn't yet —
  next phase).
- Any HA sensor, Lovelace card, or map card for this data — nothing to show
  yet until fire ingestion is proven; dashboard work is a later phase once
  there's a couple days of real data in the table.
- Boone County Sheriff log (already marked lower-priority/optional in the
  original spec).
- Firewall hardening beyond what's already in place — the LAN exposure is
  intentional per this round's feedback; revisit only if Grant wants it
  scoped further later.

## Verification

1. `sudo apt install postgresql postgresql-18-postgis-3`; confirm
   `systemctl status postgresql` and `psql --version` report PostgreSQL 18.
2. Apply `postgresql.conf`/`pg_hba.conf` changes, restart, then confirm LAN
   reachability from a second machine on `192.168.68.0/22`: `psql -h
   192.168.68.101 -U <role> -d mandi_geo -c "SELECT PostGIS_Version();"`
   should succeed with the scram password and fail cleanly for an
   unlisted source or wrong password (don't just confirm the happy path —
   confirm the access control actually restricts, not just that it allows).
3. Run `columbia-911-fire-fetch.py` standalone once; confirm rows land in
   `fire_medical_calls` with `geox`/`geoy` populated and `geom` non-null,
   and spot-check one row's transformed lat/lon against the real-world
   address it should correspond to (Columbia, MO is roughly 38.95°N,
   -92.33°W — a transformed point wildly outside that neighborhood means
   the SRID/transform is wrong).
4. Enable and start the systemd timer; confirm via `systemctl status
   columbia-911-fire-fetch.timer` and a second manual run ~5 min later that
   the `ON CONFLICT` upsert is working (re-running doesn't duplicate rows,
   new calls do get added, `fetched_at` updates on changed rows).
5. Document the new service in `~/MANDI/MANDI-plan.md` (new dated section,
   including the credentials location and the LAN-exposure decision) and
   update/create the relevant memory file(s), same as the COU Flights and
   Rachio work.
