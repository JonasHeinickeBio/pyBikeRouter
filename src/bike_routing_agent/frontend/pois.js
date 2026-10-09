/* Points of interest on the map (pure functions, no DOM access).
 *
 * The server finds and ranks POIs (poi/ package); this file turns them into popup
 * HTML, remembers the visitor's filter and works out where an added POI belongs in
 * the list of via points. Tested under node (tests/test_frontend_pois.py).
 */
(function (root) {
  "use strict";

  const ICONS = {
    viewpoint: "\u{1F304}",
    attraction: "⭐",
    historic: "\u{1F3F0}",
    museum: "\u{1F3DB}️",
    nature: "\u{1F333}",
    religious: "⛪",
    swimming: "\u{1F3CA}",
    food: "☕",
    water: "\u{1F4A7}",
    rest: "\u{1F6D6}",
    bike_service: "\u{1F527}",
  };
  const STORAGE_KEY = "pybikerouter.pois.v1";
  // At or above this many language editions a sight gets the bigger marker.
  const FAMOUS_AT = 30;
  const MAX_VIAS = 10;

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function icon(category) {
    return ICONS[category] || "\u{1F4CD}";
  }

  /** "Described in 93 languages" -- a fact about Wikidata, not a rating. "" when unknown. */
  function fameText(fame) {
    if (fame == null) return "";
    return `Described in ${fame} ${fame === 1 ? "language" : "languages"}`;
  }

  function isFamous(poi) {
    return poi.fame != null && poi.fame >= FAMOUS_AT;
  }

  function distanceText(meters) {
    if (meters == null) return "";
    if (meters < 50) return "on the route";
    return meters < 1000 ? `${Math.round(meters / 10) * 10} m from the route` : `${(meters / 1000).toFixed(1)} km from the route`;
  }

  function title(poi, categories) {
    if (poi.name) return poi.name;
    const known = (categories || []).find((c) => c.key === poi.category);
    return known ? known.label : poi.category;
  }

  /** The popup: facts, a "more" button (only when the open services may know more), "add". */
  function popupHtml(poi, categories) {
    const known = (categories || []).find((c) => c.key === poi.category);
    const meta = [known ? known.label : poi.category, fameText(poi.fame), distanceText(poi.distance_from_route_m)]
      .filter(Boolean)
      .map(escapeHtml)
      .join(" &middot; ");
    const hours = poi.opening_hours ? `<div class="poi-hours">Hours (OSM): ${escapeHtml(poi.opening_hours)}</div>` : "";
    const canInfo = Boolean(poi.wikidata || poi.wikipedia);
    return (
      `<div class="poi-popup"><strong class="poi-title">${icon(poi.category)} ${escapeHtml(title(poi, categories))}</strong>` +
      `<div class="poi-meta">${meta}</div>${hours}<div class="poi-info" aria-live="polite"></div>` +
      '<div class="poi-actions">' +
      (canInfo ? '<button type="button" class="ghost" data-act="info">Read more</button>' : "") +
      '<button type="button" class="primary" data-act="add">Add to route</button></div></div>'
    );
  }

  function safeUrl(url) {
    return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
  }

  /** What Wikipedia / Wikidata / Wikivoyage say, with links to read on. */
  function infoHtml(info) {
    if (!info) return "";
    const parts = [];
    const thumb = safeUrl(info.thumbnail_url);
    if (thumb) parts.push(`<img class="poi-thumb" src="${escapeHtml(thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">`);
    if (info.description) parts.push(`<div class="poi-desc">${escapeHtml(info.description)}</div>`);
    if (info.extract) {
      const lang = info.language ? ` lang="${escapeHtml(info.language)}"` : "";
      parts.push(`<p class="poi-extract"${lang}>${escapeHtml(info.extract)}</p>`);
    }
    if (!info.extract && !info.description) parts.push('<p class="poi-extract">No description available right now.</p>');
    const links = (info.links || [])
      .filter((l) => safeUrl(l.url))
      .map((l) => `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(l.label)}</a>`);
    if (links.length) parts.push(`<div class="poi-links">${links.join(" &middot; ")}</div>`);
    if ((info.attribution || []).length) parts.push(`<div class="poi-attr">${info.attribution.map(escapeHtml).join(" &middot; ")}</div>`);
    return parts.join("");
  }

  /** The query string of GET /v1/pois/info for a POI. */
  function infoQuery(poi, lang) {
    const q = new URLSearchParams();
    if (poi.wikidata) q.set("wikidata", poi.wikidata);
    if (poi.wikipedia) q.set("wikipedia", poi.wikipedia);
    if (poi.id) q.set("osm_id", poi.id);
    if (safeUrl(poi.website)) q.set("website", poi.website);
    q.set("lang", String(lang || "en").toLowerCase().split("-")[0]);
    return q.toString();
  }

  /* ----------------------------- remembered filter ----------------------------- */

  function defaultPrefs(categories) {
    // Sights on, services off: a map full of taps and cafes hides the sights.
    return { enabled: true, keys: (categories || []).filter((c) => c.kind === "sight").map((c) => c.key) };
  }

  function loadPrefs(storage, categories) {
    const fallback = defaultPrefs(categories);
    try {
      const raw = JSON.parse(storage.getItem(STORAGE_KEY));
      if (!raw || typeof raw.enabled !== "boolean" || !Array.isArray(raw.keys)) return fallback;
      const valid = new Set((categories || []).map((c) => c.key));
      return { enabled: raw.enabled, keys: raw.keys.filter((k) => valid.has(k)) };
    } catch {
      return fallback;
    }
  }

  function savePrefs(storage, prefs) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
      /* private window or blocked storage: the filter just is not remembered */
    }
  }

  /* ------------------------------ where does it go? ---------------------------- */

  function metersPerDegreeLon(lat) {
    return 111320 * Math.max(Math.cos((lat * Math.PI) / 180), 1e-6);
  }

  /** Distance along `coords` ([lon, lat] pairs) to the point on the line closest to (lon, lat), in metres. */
  function alongMeters(coords, lon, lat) {
    const kx = metersPerDegreeLon(lat);
    let best = Infinity;
    let bestAlong = 0;
    let along = 0;
    for (let i = 1; i < coords.length; i++) {
      const ax = (coords[i - 1][0] - lon) * kx;
      const ay = (coords[i - 1][1] - lat) * 111320;
      const dx = (coords[i][0] - coords[i - 1][0]) * kx;
      const dy = (coords[i][1] - coords[i - 1][1]) * 111320;
      const len = Math.hypot(dx, dy);
      // Parameter of the closest point on this segment to the target (the origin here).
      const t = len === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (len * len)));
      const d = Math.hypot(ax + t * dx, ay + t * dy);
      if (d < best) {
        best = d;
        bestAlong = along + t * len;
      }
      along += len;
    }
    return bestAlong;
  }

  /**
   * Index in the via list at which a POI keeps the stops in travel order.
   * `vias` are {lat, lon} or null (a place name, whose position is unknown).
   * Without a route, or with unplaceable vias, it goes last.
   */
  function insertionIndex(routeCoords, vias, poi) {
    if (!routeCoords || routeCoords.length < 2) return vias.length;
    const target = alongMeters(routeCoords, poi.lon, poi.lat);
    let index = 0;
    for (let i = 0; i < vias.length; i++) {
      if (vias[i] === null) return vias.length;
      if (alongMeters(routeCoords, vias[i].lon, vias[i].lat) <= target) index = i + 1;
    }
    return index;
  }

  /** At most `max` evenly spread points, first and last kept (keeps the request small). */
  function thin(coords, max) {
    if (coords.length <= max) return coords;
    const step = (coords.length - 1) / (max - 1);
    const out = [];
    for (let i = 0; i < max; i++) out.push(coords[Math.round(i * step)]);
    out[max - 1] = coords[coords.length - 1];
    return out;
  }

  /** What to say about the stops a plan was asked to pass. */
  function stopsNote(status, stops) {
    const names = (stops || []).map((s) => escapeHtml(s.name || s.category)).join(", ");
    switch (status) {
      case "ok":
        return stops && stops.length ? `Passing the best-known sights: ${names}.` : "";
      case "none_found":
        return "No well-known sights were found along this trip, so it was planned without stops.";
      case "unavailable":
        return "The sight lookup is not available right now, so the route was planned without stops.";
      case "unsupported":
        return "Famous-sight stops are not available for loops, so the route was planned without them.";
      case "dropped":
        return "The routing engines could not reach the sights by bike, so the route was planned without them.";
      default:
        return "";
    }
  }

  const api = {
    MAX_VIAS,
    escapeHtml,
    icon,
    fameText,
    isFamous,
    distanceText,
    popupHtml,
    infoHtml,
    infoQuery,
    defaultPrefs,
    loadPrefs,
    savePrefs,
    alongMeters,
    insertionIndex,
    thin,
    stopsNote,
  };
  root.BikePois = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
