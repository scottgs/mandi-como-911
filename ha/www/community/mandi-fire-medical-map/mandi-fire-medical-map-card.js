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
