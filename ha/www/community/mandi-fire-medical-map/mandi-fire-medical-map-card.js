const LEAFLET_JS_URL = "/local/community/mandi-fire-medical-map/leaflet.js";
const LEAFLET_CSS_URL = "/local/community/mandi-fire-medical-map/leaflet.css";
// Fallback only -- the real home location comes from hass.config.latitude/longitude
// (HA's own configured home location) at render time.
const COLUMBIA_MO = [38.9517, -92.3341];
const TWELVE_HOURS_MS = 12 * 60 * 60 * 1000;
const HOME_VIEW_MILES_ACROSS = 20;
const MILES_PER_DEGREE_LAT = 69.0;

// Bounding box roughly `milesAcross` x `milesAcross`, centered on [lat, lon].
// Longitude degrees shrink toward the poles (cos(latitude)), latitude degrees
// don't -- both are converted from the same real-world mile distance.
function computeHomeBounds(lat, lon, milesAcross) {
  const halfMiles = milesAcross / 2;
  const latDelta = halfMiles / MILES_PER_DEGREE_LAT;
  const lonDelta = halfMiles / (MILES_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180));
  return [
    [lat - latDelta, lon - lonDelta],
    [lat + latDelta, lon + lonDelta],
  ];
}

function ensureLeafletScriptLoaded() {
  if (window.L) return Promise.resolve();
  if (window.__mandiLeafletLoading) return window.__mandiLeafletLoading;

  window.__mandiLeafletLoading = new Promise((resolve, reject) => {
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
  constructor() {
    super();
    this._boundUpdateMapHeight = () => this._updateMapHeight();
  }

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
    this._initPromise = ensureLeafletScriptLoaded().then(async () => {
      this.innerHTML = `
        <link rel="stylesheet" href="${LEAFLET_CSS_URL}">
        <ha-card>
          <div class="mandi-map-header" style="padding: 8px 16px; font-size: 0.9em; color: var(--secondary-text-color);"></div>
          <div class="mandi-map"></div>
        </ha-card>
      `;
      this._headerEl = this.querySelector(".mandi-map-header");
      this._mapEl = this.querySelector(".mandi-map");

      // HA's `type: panel` view stretches the card to full *width* but not
      // full *height* -- hui-panel-view is already sized to the available
      // viewport height, it just never propagates that down to the card's
      // content. There's no reliable percentage-height chain through HA's
      // own (unstyled-by-us) hui-card/hui-panel-view wrappers, so the
      // available height is computed directly from the viewport instead.
      this._updateMapHeight();
      window.addEventListener("resize", this._boundUpdateMapHeight);

      const cssLink = this.querySelector('link[rel="stylesheet"]');
      await new Promise((resolve) => {
        if (cssLink.sheet) {
          resolve();
        } else {
          cssLink.onload = () => resolve();
          cssLink.onerror = () => resolve(); // don't hang forever if the stylesheet fails to load; map still renders, just visually degraded
        }
      });

      const homeLat = this._hass?.config?.latitude ?? COLUMBIA_MO[0];
      const homeLon = this._hass?.config?.longitude ?? COLUMBIA_MO[1];
      // zoomSnap: 0 allows fractional zoom levels -- Leaflet's default
      // (integer-only zoom) rounds fitBounds *down* to guarantee the whole
      // box stays visible, which can nearly double the shown area versus
      // what was actually requested. Fractional zoom lets it match the
      // requested ~20mi box tightly (tiles render very slightly upscaled
      // between integer levels, which is standard and not visually
      // noticeable at this scale).
      this._map = window.L.map(this._mapEl, { zoomSnap: 0 });
      this._map.fitBounds(computeHomeBounds(homeLat, homeLon, HOME_VIEW_MILES_ACROSS));
      window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors",
        maxZoom: 19,
      }).addTo(this._map);
      this._markersLayer = window.L.layerGroup().addTo(this._map);

      this._resizeObserver = new ResizeObserver(() => {
        this._map.invalidateSize();
      });
      this._resizeObserver.observe(this._mapEl);
    });
    return this._initPromise;
  }

  // Fills the remaining viewport height below the map's own top offset (see
  // the comment in _init()). Bound once in the constructor so the same
  // function reference can be added/removed from the window resize listener.
  _updateMapHeight() {
    if (!this._mapEl) return;
    const top = this._mapEl.getBoundingClientRect().top;
    const bottomMargin = 16;
    const height = Math.max(300, window.innerHeight - top - bottomMargin);
    this._mapEl.style.height = `${height}px`;
    if (this._map) this._map.invalidateSize();
  }

  disconnectedCallback() {
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
    }
    window.removeEventListener("resize", this._boundUpdateMapHeight);
  }

  _render() {
    if (!this._map || !this._hass) return;
    const stateObj = this._hass.states[this._entity];
    if (!stateObj) {
      this._headerEl.textContent = `${this._entity} not found`;
      return;
    }

    const calls = stateObj.attributes.calls || [];
    const fetchedAt = stateObj.attributes.fetched_at;
    const fetchedDisplay = stateObj.attributes.fetched_at_display || "";
    const now = Date.now();
    const recent = calls.filter((c) => {
      if (c.lat == null || c.lon == null) return false;
      const t = Date.parse(c.call_datetime);
      return !isNaN(t) && now - t <= TWELVE_HOURS_MS && now - t >= 0;
    });

    const countText = recent.length
      ? `${recent.length} call${recent.length === 1 ? "" : "s"} in the last 12 hours`
      : "No calls in the last 12 hours";

    const ageMin = Math.round((now - Date.parse(fetchedAt)) / 60000);
    let freshnessHtml;
    if (!isNaN(ageMin) && ageMin > 20) {
      freshnessHtml = `<span style="color: #e65100">⚠ Stale — last updated ${escapeHtml(
        fetchedDisplay
      )} (${ageMin}m ago)</span>`;
    } else if (!isNaN(ageMin)) {
      freshnessHtml = `<span style="color: #757575">Updated ${escapeHtml(
        fetchedDisplay
      )} (${ageMin}m ago)</span>`;
    } else {
      freshnessHtml = `<span style="color: #757575">Updated ${escapeHtml(fetchedDisplay)}</span>`;
    }

    this._headerEl.innerHTML = `${escapeHtml(countText)} — ${freshnessHtml}`;

    const dataChanged = fetchedAt !== this._lastRenderedFetchedAt;
    if (!dataChanged) return;
    this._lastRenderedFetchedAt = fetchedAt;

    this._markersLayer.clearLayers();
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
    }
    // Viewport intentionally stays fixed on the ~20mi home-centered view set
    // once in _init() -- markers render wherever they are (even off-screen,
    // for a call outside that box), but data refreshes never move the map
    // out from under a user who has manually panned/zoomed.
  }
}

customElements.define("mandi-fire-medical-map-card", MandiFireMedicalMapCard);
