/* Colouring the route by surface or by traffic (pure functions, no DOM access).
 *
 * The server sends `stretches` with a route (BRouter's OSM tags along the way): runs of vertices of
 * the route's geometry, each with a surface class and a traffic band. This file cuts the geometry
 * into the coloured pieces for a mode, adds up what each class covers, and finds the longest
 * stretch of the kind a rider wants to know about (unpaved, busy). "Not mapped" is untagged, which
 * is not the same as bad or quiet. Tested under node (tests/test_frontend_stretches.py).
 */
(function (root) {
  "use strict";

  const MODES = {
    surface: {
      label: "Surface",
      field: "surface",
      classes: [
        { key: "paved", label: "Paved", color: "#3b6fb6" },
        { key: "unpaved", label: "Unpaved", color: "#c98a1b" },
        { key: "cobbles", label: "Cobbles", color: "#8a5cc2" },
        { key: "unknown", label: "Not mapped", color: "#9aa5b1" },
      ],
      notable: "unpaved",
      note: "From the OSM surface tags along the route; “not mapped” means no tag, which says nothing about the surface.",
    },
    traffic: {
      label: "Traffic",
      field: "traffic",
      classes: [
        { key: "busy", label: "Busy roads", color: "#c43d32" },
        { key: "some", label: "Some traffic", color: "#e0a030" },
        { key: "quiet", label: "Quiet roads", color: "#4caf6a" },
        { key: "off_road", label: "Off-road and bike paths", color: "#1d7a46" },
        { key: "unknown", label: "Not mapped", color: "#9aa5b1" },
      ],
      notable: "busy",
      note: "An estimate from road class, bike lanes and speed limits, not a count of cars.",
    },
  };
  const MIN_NOTABLE_M = 300;
  const MIN_LEGEND_M = 25;

  function toRad(deg) {
    return (deg * Math.PI) / 180;
  }

  function haversine(a, b) {
    const dLat = toRad(b[1] - a[1]);
    const dLon = toRad(b[0] - a[0]);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /** The line of the candidate ([lon, lat] pairs) and the distance to each vertex, or null. */
  function line(candidate) {
    const geometry = candidate && candidate.geometry_geojson;
    const coords = geometry && geometry.type === "LineString" ? geometry.coordinates : null;
    if (!Array.isArray(coords) || coords.length < 2) return null;
    const cumulative = [0];
    for (let i = 1; i < coords.length; i++) {
      const a = coords[i - 1];
      const b = coords[i];
      if (!Array.isArray(a) || !Array.isArray(b) || !Number.isFinite(a[0]) || !Number.isFinite(b[0]) || !Number.isFinite(a[1]) || !Number.isFinite(b[1])) return null;
      cumulative.push(cumulative[i - 1] + haversine(a, b));
    }
    return { coords, cumulative };
  }

  /** The usable stretches: whole-number vertex ranges inside the line, in order, no overlaps. */
  function usable(candidate, vertexCount) {
    const out = [];
    let last = 0;
    for (const s of Array.isArray(candidate && candidate.stretches) ? candidate.stretches : []) {
      if (!s || !Number.isInteger(s.from_index) || !Number.isInteger(s.to_index)) continue;
      if (s.from_index < last || s.to_index <= s.from_index || s.to_index >= vertexCount) continue;
      out.push(s);
      last = s.to_index;
    }
    return out;
  }

  function classOf(mode, stretch) {
    const spec = MODES[mode];
    const key = stretch[spec.field];
    return spec.classes.find((c) => c.key === key) || spec.classes[spec.classes.length - 1];
  }

  /**
   * The modes worth offering for a route: those with stretches in at least one class other than
   * "not mapped" (a route with no tags at all is not coloured, not shown as one grey line).
   */
  function modes(candidate) {
    const l = line(candidate);
    if (!l) return [];
    const stretches = usable(candidate, l.coords.length);
    return Object.keys(MODES).filter((mode) => stretches.some((s) => classOf(mode, s).key !== "unknown"));
  }

  /** The coloured pieces for a mode: {key, label, color, latlngs, fromM, toM}, neighbours of one class merged. */
  function pieces(candidate, mode) {
    const l = MODES[mode] && line(candidate);
    if (!l) return [];
    const out = [];
    for (const s of usable(candidate, l.coords.length)) {
      const cls = classOf(mode, s);
      const last = out[out.length - 1];
      const slice = l.coords.slice(s.from_index, s.to_index + 1).map((p) => [p[1], p[0]]);
      if (last && last.key === cls.key && last.toIndex === s.from_index) {
        last.latlngs.push(...slice.slice(1));
        last.toIndex = s.to_index;
        last.toM = l.cumulative[s.to_index];
      } else {
        out.push({
          key: cls.key,
          label: cls.label,
          color: cls.color,
          latlngs: slice,
          fromIndex: s.from_index,
          toIndex: s.to_index,
          fromM: l.cumulative[s.from_index],
          toM: l.cumulative[s.to_index],
        });
      }
    }
    return out;
  }

  /** What each class covers: [{key, label, color, meters, share}], the largest first. */
  function legend(candidate, mode) {
    const all = pieces(candidate, mode);
    const covered = all.reduce((sum, p) => sum + (p.toM - p.fromM), 0);
    if (!(covered > 0)) return [];
    const totals = new Map();
    for (const p of all) totals.set(p.key, (totals.get(p.key) || 0) + (p.toM - p.fromM));
    return MODES[mode].classes
      .filter((c) => totals.get(c.key) >= MIN_LEGEND_M) // a few metres is a row of "0.0 km"
      .map((c) => ({ key: c.key, label: c.label, color: c.color, meters: totals.get(c.key), share: totals.get(c.key) / covered }))
      .sort((a, b) => b.meters - a.meters);
  }

  /** The longest unpaved (surface) or busy (traffic) stretch: {label, meters, atM}, or null if short or none. */
  function longest(candidate, mode) {
    const spec = MODES[mode];
    if (!spec) return null;
    let best = null;
    for (const p of pieces(candidate, mode)) {
      const meters = p.toM - p.fromM;
      if (p.key === spec.notable && (!best || meters > best.meters)) best = { label: p.label.toLowerCase(), meters, atM: p.fromM };
    }
    return best && best.meters >= MIN_NOTABLE_M ? best : null;
  }

  function km(meters) {
    return `${(meters / 1000).toFixed(1)} km`;
  }

  function percent(share) {
    return `${Math.round(share * 100)} %`;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** The legend as HTML: a colour swatch, the class, its length and share. */
  function legendHtml(rows) {
    return (rows || [])
      .map(
        (r) =>
          `<li><span class="stretch-swatch" style="background:${escapeHtml(r.color)}"></span>` +
          `<span class="stretch-label">${escapeHtml(r.label)}</span>` +
          `<span class="stretch-share">${escapeHtml(km(r.meters))} &middot; ${escapeHtml(percent(r.share))}</span></li>`,
      )
      .join("");
  }

  /** "Longest unpaved stretch: 3.2 km, from km 12.4." or "". */
  function longestText(found) {
    return found ? `Longest ${found.label} stretch: ${km(found.meters)}, from km ${(found.atM / 1000).toFixed(1)}.` : "";
  }

  const api = { MODES, modes, pieces, legend, longest, legendHtml, longestText };
  root.BikeStretches = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
