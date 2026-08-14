# Columbia, MO 911 Dashboard — Build Spec for MANDI (Home Assistant Panel)

## Goal

Build a Home Assistant panel in the MANDI system that shows the **last 24 hours of police, fire, and medical calls for service in Columbia, MO / Boone County**, sourced from the city's public dispatch pages.

No official public JSON/REST API exists for this data. All sources below are server-rendered HTML pages intended for browser viewing — this project requires a scraper/middleware layer, not a direct API integration.

---

## Data Sources

### 1. Fire & Rescue Dispatch (primary — fire/medical) — REQUIRED

- **URL:** https://www.como.gov/CMS/911dispatch/fire.php
- **Covers:** Medical response calls, fire alarms, smoke alarms, motor vehicle collisions, citizen assist/service calls, electrical hazards, "unknown problem" calls dispatched to Columbia Fire & Rescue.
- **Latency:** Near real-time (observed entries within minutes of current time).
- **Format:** Static server-rendered HTML table. Columns observed: time, call type (for non-medical rows), address/location. Medical calls are grouped separately at the top of the page without an explicit "call type" label (all are "Medical Response").
- **Pagination/filtering:** Not confirmed to have date-range filtering; page appears to show the current day's calls. Verify actual filter/query parameters when building the scraper — do not assume none exist.
- **Update cadence for scraper:** Poll every ~5 minutes given near-real-time nature.

### 2. Columbia Police Dispatch (primary — police) — REQUIRED

- **URL:** https://www.como.gov/CMS/911dispatch/police.php
- **Covers:** Traffic stops, 911 checks, disturbances, accidents, larceny, trespassing, assist-other-agency, assist-medics, suspicious incidents, etc.
- **Latency:** **Officially 6 hours delayed** per city policy. This MUST be visibly labeled on the dashboard — do not present as live/real-time.
- **Format:** Static server-rendered HTML table (time, call type, location).
- **Pagination:** Offset-based query parameter, e.g. `?offset=0`, `?offset=25`, `?offset=50` (25 records per page observed; a full day was ~80 records across 4 pages).
- **Filtering:** Page includes a form with fields for Type, Street Name/Address, Start Date, End Date — confirm exact GET/POST param names when building the scraper, and use the date filter to limit pulls to the last 24–30 hours (reduces pages fetched).
- **Update cadence for scraper:** Poll every 30–60 minutes (faster polling has no benefit given the 6-hour delay).

### 3. Boone County Sheriff Daily Incident Log (optional / secondary)

- **URL:** https://report.boonemo.gov/mrcjava/servlet/SH01_MP.I00070s
- **Covers:** County-wide sheriff's office calls (traffic stops, security checks, fire department assists, follow-ups, etc.) — overlaps with but is not identical in scope to Columbia city limits (sheriff jurisdiction is mostly unincorporated Boone County).
- **Format:** Java servlet-based report tool (not a simple static table) with filters for date range, incident type, and location; supports export to Excel/PDF. Large historical archive (500K+ records).
- **Risk:** Likely session/postback-based UI, more fragile to scrape reliably than the two como.gov pages. Treat as a **lower-priority, optional add-on** for county-wide coverage — do not block the MVP on this source.

### Sources ruled out (do not pursue)

- **opendata.como.gov** ("City of Columbia Data Portal") — domain no longer resolves (DNS failure at time of research). Defunct.
- **Columbia GIS Open Data Hub** — https://datahub-gocolumbiamo.opendata.arcgis.com/ — active, but catalog contains only planning/zoning/permits/parks/infrastructure datasets. No police, fire, or 911 call datasets published here (confirmed via full DCAT catalog review).

---

## Legal / Politeness Notes

- `como.gov/robots.txt` does **not** disallow the `/CMS/911dispatch/` path.
- It does set a site-wide `Content-Signal: ai-train=no` and explicitly disallows several named AI crawlers (including ClaudeBot). A plain Home Assistant HTTP client is not one of the named bots, but:
  - Poll at reasonable intervals (see cadences above) — do not hammer the server.
  - Do not use this data for AI training; this project is personal/informational use only.
  - Review como.gov's terms of use before deploying an automated poller.

---

## Recommended Architecture

HA's built-in `scrape` integration is a poor fit — it's designed for a single value per sensor, not multi-row tables. Recommended approach:

### Option A (preferred): Middleware script + REST sensor

1. A small Python script (BeautifulSoup + requests) that:
   - GETs `fire.php` and `police.php` (with date-filtered params for police.php where possible).
   - Parses each table into normalized records:
     ```json
     {
       "timestamp": "2026-08-13T17:38:00-05:00",
       "agency": "fire" | "police",
       "call_type": "Medical Response",
       "location": "1452 Kitty Hawk Dr"
     }
     ```
   - Filters to the rolling last-24-hours window.
   - Dedupes against previously seen records (needed especially for the paginated/delayed police feed).
   - Sorts merged results by timestamp descending.
   - Writes output as JSON, served either via a tiny local HTTP endpoint (Flask/FastAPI) or a file HA can read.
2. Home Assistant `rest` sensor (or `command_line` sensor if reading a local file) polls this middleware on a schedule:
   - Fire feed: every 5 min
   - Police feed: every 30–60 min
3. Store the merged 24-hour list as a sensor attribute (list of dicts).

### Option B (lighter-weight): `ha-multiscrape` (HACS custom component)

- Extracts multiple fields from a single HTTP GET directly inside HA config, no external middleware needed.
- Simpler to deploy, but less control over dedup/pagination logic than Option A — likely insufficient alone for the paginated police feed; may still need Option A for police.php specifically while using this for fire.php.

### Dashboard rendering

- `flex-table-card` or `auto-entities` (HACS) for a sortable table view of the merged call list — likely the best fit for MANDI panel display.
- Alternative: `markdown` card with a Jinja2 template looping over the sensor's attribute list, for a simpler feed-style view.
- Clearly label the police-derived rows as "~6 hr delayed" in the UI (e.g., a badge/column) so the two feeds aren't visually conflated as equally live.

---

## Data Schema (target normalized record)

| Field | Type | Notes |
|---|---|---|
| `timestamp` | ISO 8601 datetime | Local time (America/Chicago); infer date from page if only time is shown |
| `agency` | enum: `fire`, `police` | Source feed |
| `call_type` | string | e.g. "Medical Response", "Traffic Stop", "Disturbance" |
| `location` | string | Address or intersection as published |
| `source_url` | string | Which page/page-offset the record came from (for debugging) |

---

## Implementation Task List (for the agent)

1. Fetch and inspect the raw HTML of `fire.php` and `police.php` to confirm exact table structure, CSS selectors/classes, and any date-filter query parameters (do not assume the structure described above is exact — verify against live markup).
2. Write the Python scraper/normalizer per the schema above, including a 24-hour rolling filter and dedup logic.
3. Decide and implement the HA delivery mechanism (local REST endpoint vs. file-based `command_line` sensor).
4. Configure HA `rest`/`command_line` sensor(s) with appropriate scan intervals (fire: 5 min, police: 30–60 min).
5. Build the MANDI/Lovelace panel using `flex-table-card` or `auto-entities`, with visible delay labeling for the police feed.
6. (Optional, lower priority) Extend to Boone County Sheriff log once the core two-source MVP is working and stable.
7. **Verification:** confirm the panel matches manually-checked entries on the live como.gov pages at time of testing; confirm the 24-hour window correctly rolls off old entries; confirm scraper handles a page-structure hiccup (e.g. empty response) without crashing HA.

---

## Open Questions for the User

- Confirm what MANDI is layered on top of standard Home Assistant/Lovelace (affects whether standard HACS cards like `flex-table-card` will render as-is).
- Any interest in filtering the dashboard to a specific neighborhood/street (police.php supports a street/address filter)?
- Where should the middleware script run (HA add-on, separate always-on machine, cron job, etc.)?
