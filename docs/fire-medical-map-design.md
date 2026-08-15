# Fire/Medical 12-Hour Map — Design

**Goal:** Add a third tab to the "CoMo 911" dashboard, "Map", that always renders the
last 12 hours of Fire/Medical calls as pins on an interactive map, each pin showing
the same icon already used in the Nature column on the Fire/Medical tab.

**Not in scope:** Police calls. como.gov's police feed has never provided coordinates
(`geox`/`geoy`) — only block-level address text — so there is no reliable way to plot
them without adding an address-geocoding pipeline. Decided with Grant to skip police on
the map entirely rather than add that new dependency; the Police tab is unaffected.

## Architecture

No new sensor, package, or backend query. The existing `sensor.columbia_911_fire_medical`
(from `ha/packages/columbia_911.yaml`) already carries a rolling 24h `calls` list with
`nature`, `nature_icon`, `address`, `call_time_display`, `call_datetime`, and
`source_agency` per call. `columbia-911-fire-fetch.py`'s `RECENT_QUERY` gets extended to
also select `lon`/`lat` (extracted from the `geom` PostGIS column it already writes on
every upsert), and `build_cache_payload` adds those two fields to each call dict. That's
the entire backend change.

The new "Map" view is a custom Lovelace card that reads `sensor.columbia_911_fire_medical`
directly (via `hass.states`, standard custom-card pattern) and does its own client-side
filter down to the last 12 hours from `call_datetime` — the sensor already has a 24h
window, so a tighter window is just a filter, not a new data path.

**Why a custom card, not HA's native `map` card:** HA's native map card only plots
entities that already exist in the `device_tracker`/`person`/`geo_location` domains with
lat/long attributes. Spinning up a dynamic marker per dispatch call natively would need a
real custom Python integration (a `custom_components/` package) — a much heavier addition
than anything else in this project, which has stayed at "systemd timer + `command_line`
sensor + native/markdown Lovelace cards" throughout, with zero custom Python integrations
authored so far. A custom Lovelace card (JS only, no backend integration) reads an
existing sensor's attributes directly and needs nothing on the Python/HA-core side beyond
the `lon`/`lat` addition above.

**Why Leaflet:** Open-source (MIT), ~40KB, no API key, self-hostable. Base map tiles are
fetched live from the public OpenStreetMap tile server at view time — a live external
network dependency at render time, same category as the Google Maps links this dashboard
already uses for addresses, not something we self-host.

## Card behavior / UX

- New view on `columbia_911.yaml`: title "Map", `mdi:map` icon, `type: panel`, containing
  one custom card.
- Renders immediately on load — no button, no toggle. (Originally scoped with a
  show/hide button; simplified away once this became its own dedicated tab, since opening
  the tab already is the "reveal" action.)
- Map defaults centered on Columbia, MO; auto-fits/zooms to the visible pins' bounding box
  when there's at least one.
- Each call in the last 12h becomes a Leaflet marker using its own `nature_icon` emoji
  as a `L.divIcon`'s content (e.g. 🚑, 🔥) — no colored background disc behind it, unlike
  the Fire/Medical tab's `<font color>` text coloring. Icon-only, matching what was asked.
- Tapping a marker opens a popup: nature (prefixed with its icon), address, call time.
  No outbound Google Maps link in the popup — redundant when already looking at a map.
- A header line above the map shows a call count and "Updated Xm ago" (same freshness
  convention as the other two tabs, computed client-side in the card from the sensor's
  `fetched_at`/`fetched_at_display` attributes).
- Explicit empty state ("No calls in the last 12 hours") when the filtered list is empty
  — the map itself still renders (centered on Columbia, MO, no pins), it isn't replaced
  by a blank/error state.

## Implementation surface

All changes live in `mandi-como-911`:

- **`fetch/columbia-911-fire-fetch.py`** — extend `RECENT_QUERY` to select
  `ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat`, add `lon`/`lat` to the dict
  built in `build_cache_payload`. Small, additive diff — same shape as the `nature_icon`
  change from the previous session.
- **`ha/lovelace/columbia_911.yaml`** — add the third view containing the new card.
- **`ha/www/community/mandi-fire-medical-map/`** — new directory holding the custom card
  JS (`mandi-fire-medical-map-card.js`) plus vendored `leaflet.js`/`leaflet.css` (no
  vendored marker-image assets needed — markers are emoji `divIcon`s, not the default
  Leaflet pin graphic). Same self-hosting convention `card-mod` already established in
  this stack: fetched once from the upstream release, committed into the repo, not
  pulled from a CDN at runtime.
- **`install.sh`** — new step copying `ha/www/community/mandi-fire-medical-map/` into the
  target `${HA_CONFIG_DIR}/www/community/` and registering it as a Lovelace `module`
  resource (same `.storage/lovelace_resources` mechanism `card-mod` uses, or — if that
  proves awkward to script idempotently — printed as a manual step alongside the existing
  `configuration.yaml` reminder, decided during implementation once the actual resource-
  registration mechanics are worked out).
- **`uninstall.sh`** — mirrors the removal.
- **`README.md`** — new section describing the Map tab and its one caveat (police not
  included, and why).

## Testing / verification plan

1. Standalone-run `columbia-911-fire-fetch.py` against scratch output (same method used
   for every previous change to this script) and confirm `lon`/`lat` are present and
   sane (within the Columbia, MO area) for all rows with a non-null `geom`.
2. Load the new Map tab in a real, logged-in browser session and visually confirm pin
   placement, popup content, and the empty-state message (using a temporarily-narrowed
   window if there happen to be zero calls in the real last-12h at test time). Note: the
   headless Playwright browser on srs9 has no color-emoji font installed (established
   during the Nature-column icon work) — pin *positions* and popup *text* can be verified
   visually there, but the *emoji glyphs themselves* rendering correctly needs a real
   end-user browser, same caveat as before.
3. Validate `columbia_911.yaml` via `check_config` before restarting HA, per this
   project's standing practice.
4. Confirm the other two tabs (Fire/Medical, Police) are unaffected by the new view.
