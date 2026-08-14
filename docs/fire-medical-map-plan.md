# Fire/Medical 12-Hour Map Implementation Plan

> **For agentic workers:** This is infrastructure/frontend work on a live home server,
> not a tested software library — there is no pytest suite to drive TDD. Execute
> task-by-task via `superpowers:executing-plans` conventions (checkboxes, one task at a
> time, commit after each), treating "run the verification command / load it in a real
> browser and look" as the equivalent of "run the test." Follow
> `superpowers:verification-before-completion` before declaring any task done.

**Goal:** Add a third "Map" view to the `columbia_911.yaml` dashboard that always renders
the last 12 hours of Fire/Medical calls as Leaflet map pins, each using the same emoji
already shown in the Nature column. Design spec: `docs/fire-medical-map-design.md`.

**Architecture:** A custom Lovelace card (`mandi-fire-medical-map-card.js`), vendored
Leaflet, and a small extension to `columbia-911-fire-fetch.py` to expose `lon`/`lat` on
the existing `sensor.columbia_911_fire_medical`. No new backend sensor/package, no
custom Python integration. Police is explicitly out of scope (no coordinates available).

## Global Constraints

- No new HA custom Python integration — this stays JS-only on the frontend, matching the
  design decision.
- Leaflet is vendored (self-hosted under `ha/www/`), not loaded from a CDN at runtime —
  same convention this repo already follows for `card-mod`.
- Every step that touches the live `homeassistant/config/` tree ends with
  `docker exec homeassistant python3 -m homeassistant --script check_config --config /config`
  before restarting, per this project's standing practice.
- Commit after each task. Do not push without being asked (per `[[feedback_mandi_git_commits]]`).
- Police calls are never touched by this work — the fire fetch script only gains fields,
  the police fetch script is untouched.

---

### Task 1: Add `lon`/`lat` to the fire/medical fetch script

**Files:**
- Modify: `fetch/columbia-911-fire-fetch.py`

**Interfaces:**
- Produces: each dict in `build_cache_payload`'s `calls` list gains `lon` (float or
  `None`) and `lat` (float or `None`) keys, alongside the existing `nature_icon` key
  added in the previous session.

- [ ] **Step 1: Extend `RECENT_QUERY`**

Find:
```python
RECENT_QUERY = """
SELECT in_num, call_datetime, address, nature, source_agency, patrol_area
FROM fire_medical_calls
WHERE call_datetime >= %(since)s
ORDER BY call_datetime DESC
"""
```
Replace with:
```python
RECENT_QUERY = """
SELECT in_num, call_datetime, address, nature, source_agency, patrol_area,
       ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat
FROM fire_medical_calls
WHERE call_datetime >= %(since)s
ORDER BY call_datetime DESC
"""
```

- [ ] **Step 2: Add `lon`/`lat` to the per-call dict in `build_cache_payload`**

Find:
```python
            "nature": row["nature"],
            "nature_category": classify_nature(row["nature"]),
            "nature_icon": nature_icon(row["nature"]),
            "source_agency": row["source_agency"],
            "patrol_area": row["patrol_area"],
```
Replace with:
```python
            "nature": row["nature"],
            "nature_category": classify_nature(row["nature"]),
            "nature_icon": nature_icon(row["nature"]),
            "source_agency": row["source_agency"],
            "patrol_area": row["patrol_area"],
            "lon": row["lon"],
            "lat": row["lat"],
```

- [ ] **Step 3: Verify standalone against scratch output**

```bash
mkdir -p /tmp/scratch-www/columbia_911
HA_WWW_DIR=/tmp/scratch-www MANDI_GEO_DB_PASSWORD='<the real password from /etc/mandi/como-911.env>' \
  python3 fetch/columbia-911-fire-fetch.py
python3 -c "
import json
d = json.load(open('/tmp/scratch-www/columbia_911/fire_medical.json'))
for c in d['calls'][:5]:
    print(c['nature'], c['lon'], c['lat'])
"
rm -rf /tmp/scratch-www
```
Expected: `upserted N fire/medical calls` printed, then 5 lines each showing a `nature`
string with a `lon` around `-92.3` and `lat` around `38.9`–`39.0` (Columbia, MO's real
coordinate range) — not `None`/`null`, confirming the geometry extraction works.

- [ ] **Step 4: Commit**

```bash
git add fetch/columbia-911-fire-fetch.py
git commit -m "Add lon/lat to the fire/medical cache for the upcoming map view"
```

---

### Task 2: Vendor Leaflet

**Files:**
- Create: `ha/www/community/mandi-fire-medical-map/leaflet.js`
- Create: `ha/www/community/mandi-fire-medical-map/leaflet.css`

- [ ] **Step 1: Fetch the pinned release (1.9.4, the current stable at time of writing)**

```bash
mkdir -p ha/www/community/mandi-fire-medical-map
curl -sL -o ha/www/community/mandi-fire-medical-map/leaflet.js \
  https://unpkg.com/leaflet@1.9.4/dist/leaflet.js
curl -sL -o ha/www/community/mandi-fire-medical-map/leaflet.css \
  https://unpkg.com/leaflet@1.9.4/dist/leaflet.css
```

- [ ] **Step 2: Verify integrity against the actual hash of the file fetched during
  plan-writing** (computed directly via `openssl dgst -sha384 -binary <file> | openssl
  base64 -A` against the real files at the time this plan was written — not copied from a
  webpage, since this is the only way to be sure it's not a stale/wrong published value)

```bash
openssl dgst -sha384 -binary ha/www/community/mandi-fire-medical-map/leaflet.js | openssl base64 -A
openssl dgst -sha384 -binary ha/www/community/mandi-fire-medical-map/leaflet.css | openssl base64 -A
```
Expected (verified directly against `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js` and
`.css` while writing this plan — 147552 bytes / 14806 bytes respectively):
- `leaflet.js`: `cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH`
- `leaflet.css`: `sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H`

  If the hash doesn't match, stop and investigate (unpkg served different bytes than
  during plan-writing — re-verify against leafletjs.com's own published SRI hash for
  1.9.4 before proceeding) rather than continuing with an unverified file.

- [ ] **Step 3: Confirm no marker-image assets are needed**

```bash
grep -n "iconUrl\|_getIconUrl\|marker-icon" ha/www/community/mandi-fire-medical-map/leaflet.js | head -5
```
This confirms Leaflet's default marker image loader exists in the file (it's fine that it
does — we simply never call `L.marker()` without an explicit `icon:` option, so it's
never invoked; Task 3's card code always passes a `divIcon`).

- [ ] **Step 4: Commit**

```bash
git add ha/www/community/mandi-fire-medical-map/leaflet.js ha/www/community/mandi-fire-medical-map/leaflet.css
git commit -m "Vendor Leaflet 1.9.4 for the fire/medical map card"
```

---

### Task 3: Write the custom Lovelace card

**Files:**
- Create: `ha/www/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js`

**Interfaces:**
- Consumes: `hass.states['sensor.columbia_911_fire_medical'].attributes.calls` (list of
  dicts with `nature`, `nature_icon`, `address`, `call_datetime`, `call_time_display`,
  `lon`, `lat` — `lon`/`lat` from Task 1), `.attributes.fetched_at_display`.
- Produces: custom element `<mandi-fire-medical-map-card>`, registered via
  `customElements.define`, consumed by the Lovelace config added in Task 4 as
  `type: custom:mandi-fire-medical-map-card`.

- [ ] **Step 1: Write the card file**

```js
const LEAFLET_JS_URL = "/local/community/mandi-fire-medical-map/leaflet.js";
const LEAFLET_CSS_URL = "/local/community/mandi-fire-medical-map/leaflet.css";
const COLUMBIA_MO = [38.9517, -92.3341];
const TWELVE_HOURS_MS = 12 * 60 * 60 * 1000;

function ensureLeafletLoaded() {
  if (window.L) return Promise.resolve();
  if (window.__mandiLeafletLoading) return window.__mandiLeafletLoading;

  window.__mandiLeafletLoading = new Promise((resolve, reject) => {
    if (!document.querySelector(`link[href="${LEAFLET_CSS_URL}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = LEAFLET_CSS_URL;
      document.head.appendChild(link);
    }
    const script = document.createElement("script");
    script.src = LEAFLET_JS_URL;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load Leaflet"));
    document.head.appendChild(script);
  });
  return window.__mandiLeafletLoading;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

class MandiFireMedicalMapCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._entity = this._config.entity || "sensor.columbia_911_fire_medical";
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    this._hass = hass;
    this._init().then(() => this._render());
  }

  _init() {
    if (this._initPromise) return this._initPromise;
    this._initPromise = ensureLeafletLoaded().then(() => {
      this.innerHTML = `
        <ha-card>
          <div class="mandi-map-header" style="padding: 8px 16px; font-size: 0.9em; color: var(--secondary-text-color);"></div>
          <div class="mandi-map" style="height: 500px;"></div>
        </ha-card>
      `;
      this._headerEl = this.querySelector(".mandi-map-header");
      this._mapEl = this.querySelector(".mandi-map");
      this._map = window.L.map(this._mapEl).setView(COLUMBIA_MO, 12);
      window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors",
        maxZoom: 19,
      }).addTo(this._map);
      this._markersLayer = window.L.layerGroup().addTo(this._map);
    });
    return this._initPromise;
  }

  _render() {
    if (!this._map || !this._hass) return;
    const stateObj = this._hass.states[this._entity];
    if (!stateObj) {
      this._headerEl.textContent = `${this._entity} not found`;
      return;
    }

    const calls = stateObj.attributes.calls || [];
    const fetchedDisplay = stateObj.attributes.fetched_at_display || "";
    const now = Date.now();
    const recent = calls.filter((c) => {
      if (c.lat == null || c.lon == null) return false;
      const t = Date.parse(c.call_datetime);
      return !isNaN(t) && now - t <= TWELVE_HOURS_MS && now - t >= 0;
    });

    this._headerEl.textContent = recent.length
      ? `${recent.length} call${recent.length === 1 ? "" : "s"} in the last 12 hours — updated ${fetchedDisplay}`
      : `No calls in the last 12 hours — updated ${fetchedDisplay}`;

    this._markersLayer.clearLayers();
    const bounds = [];
    for (const c of recent) {
      const icon = window.L.divIcon({
        html: `<div style="font-size: 22px; line-height: 1;">${c.nature_icon || "📍"}</div>`,
        className: "mandi-fire-medical-marker",
        iconSize: [24, 24],
      });
      const marker = window.L.marker([c.lat, c.lon], { icon });
      marker.bindPopup(
        `<b>${c.nature_icon || ""} ${escapeHtml(c.nature)}</b><br>` +
          `${escapeHtml(c.address)}<br>${escapeHtml(c.call_time_display)}`
      );
      marker.addTo(this._markersLayer);
      bounds.push([c.lat, c.lon]);
    }

    if (bounds.length) {
      this._map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
    } else {
      this._map.setView(COLUMBIA_MO, 12);
    }
  }
}

customElements.define("mandi-fire-medical-map-card", MandiFireMedicalMapCard);
```

- [ ] **Step 2: Syntax-check the file**

```bash
node --check ha/www/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js && echo OK
```
Expected: `OK`. If `node` isn't installed on srs9, fall back to
`python3 -c "import subprocess; ..."` isn't applicable for JS — instead visually re-read
the file for balanced braces/parens before proceeding, and let Task 6's real-browser load
be the actual syntax proof (a JS syntax error surfaces immediately as a console error
there).

- [ ] **Step 3: Commit**

```bash
git add ha/www/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js
git commit -m "Add custom Lovelace card rendering fire/medical calls on a Leaflet map"
```

---

### Task 4: Add the Map view to the dashboard

**Files:**
- Modify: `ha/lovelace/columbia_911.yaml`

- [ ] **Step 1: Read the current file to find the end of the views list**

```bash
grep -n "^  - title:\|^views:" ha/lovelace/columbia_911.yaml
```
(Confirms the two existing views' titles/paths and the exact insertion point — append
after the last view.)

- [ ] **Step 2: Append the new view**

```yaml
  - title: Map
    path: map
    icon: mdi:map
    type: panel
    cards:
      - type: custom:mandi-fire-medical-map-card
        entity: sensor.columbia_911_fire_medical
```

- [ ] **Step 3: Validate YAML parses**

```bash
python3 -c "import yaml; d = yaml.safe_load(open('ha/lovelace/columbia_911.yaml')); print([v['title'] for v in d['views']])"
```
Expected: `['Near Real-time Fire/Medical', 'Delayed Police', 'Map']` (exact first two
titles per the current live file — confirm against it if they differ from this plan's
assumption).

- [ ] **Step 4: Commit**

```bash
git add ha/lovelace/columbia_911.yaml
git commit -m "Add Map view to the CoMo 911 dashboard"
```

---

### Task 5: Wire the new card + Leaflet + resource registration into `install.sh`/`uninstall.sh`

**Files:**
- Modify: `install.sh`
- Modify: `uninstall.sh`

**Interfaces:**
- Produces: `install.sh` function `register_lovelace_resource(ha_config_dir, resource_url)`,
  idempotent (checks `.storage/lovelace_resources` for an existing entry with the same
  `url` before appending).

- [ ] **Step 1: Add a `register_lovelace_resource` function to `install.sh`**

Insert near `provision_db`:
```bash
register_lovelace_resource() {
  local ha_config_dir="$1"
  local resource_url="$2"
  python3 - "${ha_config_dir}/.storage/lovelace_resources" "$resource_url" <<'PYEOF'
import json, sys, uuid

path, url = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = json.load(f)
items = data["data"]["items"]
if any(i["url"] == url for i in items):
    print(f"lovelace resource already registered: {url}")
else:
    items.append({"id": uuid.uuid4().hex, "url": url, "type": "module"})
    with open(path, "w") as f:
        json.dump(data, f)
    print(f"registered lovelace resource: {url}")
PYEOF
}
```

- [ ] **Step 2: Add a step 4b to `install.sh`'s main flow, after the existing HA
  dashboard/package copy step**

```bash
echo "== 4b/5: fire/medical map card =="
mkdir -p "${HA_CONFIG_DIR}/www/community/mandi-fire-medical-map"
cp "$SCRIPT_DIR/ha/www/community/mandi-fire-medical-map/"*.js "$SCRIPT_DIR/ha/www/community/mandi-fire-medical-map/"*.css \
  "${HA_CONFIG_DIR}/www/community/mandi-fire-medical-map/"
register_lovelace_resource "${HA_CONFIG_DIR}" "/local/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js"
```
(Leaflet's own `leaflet.js`/`leaflet.css` are copied by the same glob but deliberately
*not* registered as a Lovelace resource — the card itself loads them dynamically via
`ensureLeafletLoaded()`, only the card JS is a real Lovelace module resource.)

- [ ] **Step 3: Add the mirror-image removal to `uninstall.sh`**

```bash
echo "== fire/medical map card =="
rm -rf "${HA_CONFIG_DIR}/www/community/mandi-fire-medical-map"
python3 - "${HA_CONFIG_DIR}/.storage/lovelace_resources" <<'PYEOF'
import json, sys

path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
before = len(data["data"]["items"])
data["data"]["items"] = [
    i for i in data["data"]["items"]
    if i["url"] != "/local/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js"
]
if len(data["data"]["items"]) != before:
    with open(path, "w") as f:
        json.dump(data, f)
    print("removed fire/medical map card lovelace resource")
PYEOF
```

- [ ] **Step 4: Test `register_lovelace_resource` against a throwaway copy of the real
  resources file, twice (to prove idempotency), before running it for real**

```bash
cp ~/homeassistant/config/.storage/lovelace_resources /tmp/lovelace_resources_test.json
python3 - /tmp/lovelace_resources_test.json "/local/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js" <<'PYEOF'
import json, sys, uuid
path, url = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = json.load(f)
items = data["data"]["items"]
if any(i["url"] == url for i in items):
    print(f"lovelace resource already registered: {url}")
else:
    items.append({"id": uuid.uuid4().hex, "url": url, "type": "module"})
    with open(path, "w") as f:
        json.dump(data, f)
    print(f"registered lovelace resource: {url}")
PYEOF
python3 -c "import json; print(len(json.load(open('/tmp/lovelace_resources_test.json'))['data']['items']))"
# run the exact same snippet again here (copy-paste), expect "already registered", and the
# item count from the line above must be unchanged
rm /tmp/lovelace_resources_test.json
```
Expected: first run prints `registered lovelace resource: ...` and the item count grows
by 1 versus the original file; second run prints `already registered` and the count does
not grow further.

- [ ] **Step 5: Commit**

```bash
git add install.sh uninstall.sh
git commit -m "install.sh/uninstall.sh: deploy the fire/medical map card and register/deregister its Lovelace resource"
```

---

### Task 6: Deploy to the live srs9 install and verify

**Files:** none (deployment only — copies already-committed repo files onto the live host)

- [ ] **Step 1: Re-run the relevant parts of `install.sh` against the live config**
  (the DB/systemd steps will no-op since they're already provisioned; this exercises the
  new map-card step)

```bash
cd ~/repos/mandi-como-911 && ./install.sh scottgs ~/repos/mandi-como-911 ~/homeassistant/config
```
Expected output includes `registered lovelace resource: /local/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js`
(first run) — confirm the file `ls ~/homeassistant/config/www/community/mandi-fire-medical-map/`
shows all three files (`leaflet.js`, `leaflet.css`, `mandi-fire-medical-map-card.js`).

- [ ] **Step 2: Copy the updated dashboard YAML into the live config and validate**

```bash
cp ~/repos/mandi-como-911/ha/lovelace/columbia_911.yaml ~/homeassistant/config/lovelace/columbia_911.yaml
docker exec homeassistant python3 -m homeassistant --script check_config --config /config
```
Expected: exit code 0, no errors.

- [ ] **Step 3: Restart HA and wait for it to come back**

```bash
docker compose -f ~/homeassistant/docker-compose.yaml restart homeassistant
until curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8123/ | grep -q 200; do sleep 2; done
```

- [ ] **Step 4: Run the fetch script once to confirm the live cache now has `lon`/`lat`**

```bash
sudo systemctl start columbia-911-fire-fetch.service
sleep 2
python3 -c "
import json
d = json.load(open('/home/scottgs/homeassistant/config/www/columbia_911/fire_medical.json'))
print(d['calls'][0]['lon'], d['calls'][0]['lat'])
"
```
Expected: two real numbers, not `None`.

- [ ] **Step 5: Load the Map tab in a real, logged-in Playwright browser session**

Navigate to `http://localhost:8123/columbia-911-dashboard/map`, screenshot it, and check
the browser console for JS errors (a broken `customElements.define` or a Leaflet load
failure will show up there immediately). Confirm: the header line shows a call count and
"updated" text; if there are pins, confirm via DOM/JS evaluation (not just the screenshot,
per the established emoji-rendering caveat on this host) that marker count matches the
header's count and that clicking/opening a popup shows nature+address+time. If there are
zero calls in the real last-12h window at test time, confirm the empty-state message
instead and don't treat that as a failure.

- [ ] **Step 6: Confirm the other two tabs are unaffected**

Navigate to both the Fire/Medical and Police tabs, confirm they still render exactly as
before (screenshot or DOM check).

---

### Task 7: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a short section documenting the Map tab**, covering: what it shows
  (last 12h of Fire/Medical, no Police — and why, one sentence pointing back at the
  no-coordinates fact), that it's a custom Lovelace card with vendored Leaflet (not a
  HACS dependency), and that `install.sh`/`uninstall.sh` handle deploying/removing it
  including the Lovelace resource registration.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "README: document the Map tab"
```

---

### Task 8: Final report

- [ ] **Step 1:** `git -C ~/repos/mandi-como-911 log --oneline @{u}..` — report the
  unpushed commit count. Do not push without being asked, per this project's standing
  practice.
