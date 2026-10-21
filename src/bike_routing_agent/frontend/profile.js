/* The elevation profile of a route (pure functions, no DOM access).
 *
 * A candidate's geometry carries elevation per vertex ([lon, lat, ele]); this module turns it into
 * what a rider wants to see: the profile over distance, the steepest stretch, the real climbs and a
 * chart. Tested under node (tests/test_frontend_profile.py).
 *
 * The raw elevations are noisy (a vertex every ~20 m, a metre or so of jitter: summed up they
 * overstate the climbing by 10-15 % against the engine's own figure), so everything here works on a
 * smoothed series. The totals shown elsewhere on the page stay the engine's.
 */
(function (root) {
  "use strict";

  const EARTH_RADIUS_M = 6371008.8;
  const SMOOTH_WINDOW_M = 100; // elevations are averaged over this much of the route
  const STEP_M = 50; // the resampled series the grade and climb logic run on
  const GRADE_WINDOW_M = 100; // "the steepest 100 m"
  const MIN_ROUTE_M = 200; // shorter than this is not worth a chart
  const MIN_Y_RANGE_M = 40; // a flat route must look flat, not like a mountain range
  // The chart and the map are cut into grade colours at the same points: one per 25 m of route,
  // at most this many for a long one (more would be a heavy drawing nobody can tell apart).
  const PIECE_STEP_M = 25;
  const MAX_SAMPLES = 240;
  const COLOR_WINDOW_M = 300; // the grade a stretch of the chart is coloured by is taken over this much

  // A climb: at least this much gain, this long and this steep on average; a dip of up to DIP_M
  // inside it does not end it.
  const CLIMB_MIN_GAIN_M = 25;
  const CLIMB_MIN_LENGTH_M = 400;
  const CLIMB_MIN_GRADE = 0.03;
  const DIP_M = 10;

  /** Grade bands (percent), steepest descent first; the colour of the chart under that stretch. */
  const GRADE_BANDS = [
    { upTo: -8, color: "#1d4e89", label: "steep descent" },
    { upTo: -3, color: "#3f8fcf", label: "descent" },
    { upTo: 3, color: "#8aa39b", label: "flat" },
    { upTo: 6, color: "#e0c341", label: "climb" },
    { upTo: 10, color: "#e8892b", label: "steep climb" },
    { upTo: Infinity, color: "#c43d32", label: "very steep climb" },
  ];

  function haversine(a, b) {
    const phi1 = (a[1] * Math.PI) / 180;
    const phi2 = (b[1] * Math.PI) / 180;
    const dphi = phi2 - phi1;
    const dlambda = ((b[0] - a[0]) * Math.PI) / 180;
    const h = Math.sin(dphi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlambda / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /** The vertices of a (Multi)LineString as one list; [] for any other geometry. */
  function vertices(geometry) {
    if (!geometry) return [];
    if (geometry.type === "LineString") return geometry.coordinates || [];
    if (geometry.type === "MultiLineString") return (geometry.coordinates || []).flat();
    return [];
  }

  /** Elevations averaged over `windowM` of route around each vertex (distance-weighted window). */
  function smooth(cum, ele, windowM) {
    const half = windowM / 2;
    const out = new Array(ele.length);
    let lo = 0;
    let hi = 0;
    let sum = 0;
    for (let i = 0; i < ele.length; i++) {
      while (hi < ele.length && cum[hi] <= cum[i] + half) sum += ele[hi++];
      while (cum[lo] < cum[i] - half) sum -= ele[lo++];
      out[i] = sum / (hi - lo);
    }
    return out;
  }

  /** Linear interpolation of `values` (at distances `cum`, ascending) at distance `d`. */
  function interpolate(cum, values, d) {
    if (d <= cum[0]) return values[0];
    const last = cum.length - 1;
    if (d >= cum[last]) return values[last];
    let lo = 0;
    let hi = last;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] <= d) lo = mid;
      else hi = mid;
    }
    const span = cum[hi] - cum[lo];
    return span === 0 ? values[lo] : values[lo] + ((values[hi] - values[lo]) * (d - cum[lo])) / span;
  }

  /** The number of sample points the chart and the map colouring share (intervals = points - 1). */
  function sampleCount(profile) {
    return Math.min(MAX_SAMPLES, Math.max(2, Math.round(profile.totalM / PIECE_STEP_M) + 1));
  }

  /**
   * The profile of a geometry, or null when there is nothing to draw: no elevation on every vertex
   * (a profile with holes would be a guess), fewer than two vertices or a route under 200 m.
   */
  function build(geometry) {
    const coords = vertices(geometry);
    if (coords.length < 2) return null;
    if (!coords.every((p) => Array.isArray(p) && p.length > 2 && Number.isFinite(p[2]))) return null;
    const cum = [0];
    for (let i = 1; i < coords.length; i++) cum.push(cum[i - 1] + haversine(coords[i - 1], coords[i]));
    const totalM = cum[cum.length - 1];
    if (totalM < MIN_ROUTE_M) return null;

    const ele = smooth(
      cum,
      coords.map((p) => p[2]),
      SMOOTH_WINDOW_M,
    );
    // The series the grade and the climbs run on: every STEP_M of route, the end included.
    const count = Math.max(2, Math.round(totalM / STEP_M) + 1);
    const d = [];
    const e = [];
    for (let i = 0; i < count; i++) {
      const at = (totalM * i) / (count - 1);
      d.push(at);
      e.push(interpolate(cum, ele, at));
    }
    let min = Infinity;
    let max = -Infinity;
    for (const v of e) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    return { coords, cum, ele, totalM, min, max, d, e };
  }

  /** The steepest stretch of GRADE_WINDOW_M, as { grade (fraction), atM } (the start of it). */
  function steepest(profile) {
    const span = Math.max(1, Math.round(GRADE_WINDOW_M / STEP_M));
    let best = { grade: 0, atM: 0 };
    for (let i = 0; i + span < profile.e.length; i++) {
      const run = profile.d[i + span] - profile.d[i];
      if (run <= 0) continue;
      const grade = (profile.e[i + span] - profile.e[i]) / run;
      if (Math.abs(grade) > Math.abs(best.grade)) best = { grade, atM: profile.d[i] };
    }
    return best;
  }

  /** The climbs of a profile worth naming, biggest gain first: { fromM, lengthM, gainM, grade }. */
  function climbs(profile, limit) {
    const { d, e } = profile;
    const found = [];
    const consider = (a, b) => {
      const gain = e[b] - e[a];
      const length = d[b] - d[a];
      if (gain >= CLIMB_MIN_GAIN_M && length >= CLIMB_MIN_LENGTH_M && gain / length >= CLIMB_MIN_GRADE) {
        found.push({ fromM: d[a], lengthM: length, gainM: gain, grade: gain / length });
      }
    };
    let start = 0;
    let peak = 0;
    for (let i = 1; i < e.length; i++) {
      if (e[i] >= e[peak]) {
        peak = i;
      } else if (e[peak] - e[i] >= DIP_M) {
        consider(start, peak); // it came down far enough: that climb is over
        start = i;
        peak = i;
      }
      if (peak === start && e[i] < e[start]) {
        start = i; // still looking for the foot of the next one
        peak = i;
      }
    }
    consider(start, peak);
    found.sort((a, b) => b.gainM - a.gainM);
    return found.slice(0, limit == null ? 3 : limit);
  }

  /** Where the rider is at `distanceM` along the route: position, elevation and the local grade. */
  function at(profile, distanceM) {
    const dist = Math.max(0, Math.min(profile.totalM, distanceM));
    const before = Math.max(0, dist - GRADE_WINDOW_M / 2);
    const after = Math.min(profile.totalM, dist + GRADE_WINDOW_M / 2);
    const run = after - before;
    const e0 = interpolate(profile.cum, profile.ele, before);
    const e1 = interpolate(profile.cum, profile.ele, after);
    const lon = interpolate(profile.cum, profile.coords.map((p) => p[0]), dist);
    const lat = interpolate(profile.cum, profile.coords.map((p) => p[1]), dist);
    return {
      distanceM: dist,
      elevationM: interpolate(profile.cum, profile.ele, dist),
      grade: run > 0 ? (e1 - e0) / run : 0,
      lon,
      lat,
    };
  }

  function bandOf(grade) {
    const percent = grade * 100;
    return GRADE_BANDS.find((b) => percent < b.upTo) || GRADE_BANDS[GRADE_BANDS.length - 1];
  }

  /** A round step (1, 2, 5, 10, 20, 50 ... km) that gives at most `maxTicks` ticks over `totalKm`. */
  function tickStepKm(totalKm, maxTicks) {
    for (const step of [0.5, 1, 2, 5, 10, 20, 50, 100, 200]) {
      if (totalKm / step <= maxTicks) return step;
    }
    return 500;
  }

  function fmtKm(km) {
    return Number.isInteger(km) ? String(km) : km.toFixed(1);
  }

  /**
   * The chart as an SVG string: the area under the elevation, each stretch coloured by its grade,
   * the elevation line on top, kilometres along the bottom and the lowest and highest elevation on
   * the left. The viewBox is fixed (it scales with its container); the plot area is
   * [left, right] x [top, bottom] in those units, given as data attributes for the pointer logic.
   */
  function chartSvg(profile, options) {
    const o = options || {};
    const width = o.width || 340; // about the width of the sidebar: text keeps its size when scaled
    const height = o.height || 150;
    const pad = { left: 38, right: 8, top: 8, bottom: 20 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;

    let low = Math.floor(profile.min / 10) * 10;
    let high = Math.ceil(profile.max / 10) * 10;
    if (high - low < MIN_Y_RANGE_M) {
      const middle = (low + high) / 2;
      low = middle - MIN_Y_RANGE_M / 2;
      high = middle + MIN_Y_RANGE_M / 2;
    }
    const x = (m) => pad.left + (m / profile.totalM) * plotW;
    const y = (m) => pad.top + plotH - ((m - low) / (high - low)) * plotH;
    const base = pad.top + plotH;

    const samples = sampleCount(profile);
    const pts = [];
    for (let i = 0; i < samples; i++) {
      const m = (profile.totalM * i) / (samples - 1);
      pts.push({ m, e: interpolate(profile.cum, profile.ele, m) });
    }
    const fixed = (v) => v.toFixed(1);
    const areas = [];
    for (let i = 0; i + 1 < pts.length; i++) {
      const a = pts[i];
      const b = pts[i + 1];
      // Coloured by the grade over a wider stretch around it: over 25 m the smoothed elevation
      // still wobbles and the chart would flicker between colours.
      const mid = (a.m + b.m) / 2;
      const from = Math.max(0, mid - COLOR_WINDOW_M / 2);
      const to = Math.min(profile.totalM, mid + COLOR_WINDOW_M / 2);
      const grade =
        (interpolate(profile.cum, profile.ele, to) - interpolate(profile.cum, profile.ele, from)) /
        Math.max(1, to - from);
      areas.push(
        `<polygon fill="${bandOf(grade).color}" points="${fixed(x(a.m))},${fixed(y(a.e))} ` +
          `${fixed(x(b.m))},${fixed(y(b.e))} ${fixed(x(b.m))},${fixed(base)} ${fixed(x(a.m))},${fixed(base)}"/>`,
      );
    }
    const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${fixed(x(p.m))},${fixed(y(p.e))}`).join(" ");

    const totalKm = profile.totalM / 1000;
    const step = tickStepKm(totalKm, 6);
    const ticks = [];
    for (let km = 0; km <= totalKm + 1e-9; km += step) {
      const px = x(km * 1000);
      ticks.push(
        `<line class="profile-tick" x1="${fixed(px)}" x2="${fixed(px)}" y1="${base}" y2="${base + 3}"/>` +
          `<text class="profile-label" x="${fixed(px)}" y="${height - 4}" text-anchor="middle">${fmtKm(km)}${km === 0 ? " km" : ""}</text>`,
      );
    }
    const label = `Elevation profile: ${fmtKm(Math.round(totalKm * 10) / 10)} km, from ${Math.round(profile.min)} m to ${Math.round(profile.max)} m`;
    return (
      `<svg class="profile-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label}" ` +
      `data-left="${pad.left}" data-right="${pad.left + plotW}" data-width="${width}">` +
      `<g class="profile-areas">${areas.join("")}</g>` +
      `<path class="profile-line" d="${line}" fill="none"/>` +
      `<text class="profile-label" x="${pad.left - 4}" y="${pad.top + 8}" text-anchor="end">${Math.round(profile.max)} m</text>` +
      `<text class="profile-label" x="${pad.left - 4}" y="${base}" text-anchor="end">${Math.round(profile.min)} m</text>` +
      `<line class="profile-axis" x1="${pad.left}" x2="${pad.left + plotW}" y1="${base}" y2="${base}"/>` +
      ticks.join("") +
      `<g class="profile-cursor" hidden><line class="profile-cursor-line" x1="0" x2="0" y1="${pad.top}" y2="${base}"/></g>` +
      "</svg>"
    );
  }

  const MIN_LEGEND_M = 25; // a band of a few metres is not a legend row

  /**
   * The route cut into runs of one grade band, for colouring it on the map: [{key, label, color,
   * latlngs, fromM, toM}], neighbours of one band merged. The grade is the one the chart is coloured
   * by (taken over COLOR_WINDOW_M around each point), so the two agree.
   */
  function bandPieces(profile) {
    const lons = profile.coords.map((p) => p[0]);
    const lats = profile.coords.map((p) => p[1]);
    const point = (m) => [interpolate(profile.cum, lats, m), interpolate(profile.cum, lons, m)];
    const steps = sampleCount(profile) - 1;
    const runs = [];
    for (let i = 0; i < steps; i++) {
      const from = (profile.totalM * i) / steps;
      const to = (profile.totalM * (i + 1)) / steps;
      const mid = (from + to) / 2;
      const lo = Math.max(0, mid - COLOR_WINDOW_M / 2);
      const hi = Math.min(profile.totalM, mid + COLOR_WINDOW_M / 2);
      const grade =
        (interpolate(profile.cum, profile.ele, hi) - interpolate(profile.cum, profile.ele, lo)) / Math.max(1, hi - lo);
      const band = bandOf(grade);
      const last = runs[runs.length - 1];
      if (last && last.band === band) last.toM = to;
      else runs.push({ band, fromM: from, toM: to });
    }
    return runs.map((run) => {
      const latlngs = [point(run.fromM)];
      for (let v = 0; v < profile.cum.length; v++) {
        if (profile.cum[v] > run.fromM && profile.cum[v] < run.toM) latlngs.push([lats[v], lons[v]]);
      }
      latlngs.push(point(run.toM));
      return { key: run.band.label, label: run.band.label, color: run.band.color, latlngs, fromM: run.fromM, toM: run.toM };
    });
  }

  /** What each grade band covers: [{key, label, color, meters, share}] from steep descent to very steep climb. */
  function bandLegend(pieces) {
    const total = (pieces || []).reduce((sum, p) => sum + (p.toM - p.fromM), 0);
    if (!(total > 0)) return [];
    return GRADE_BANDS.map((b) => {
      const meters = pieces.filter((p) => p.key === b.label).reduce((sum, p) => sum + (p.toM - p.fromM), 0);
      return { key: b.label, label: b.label.charAt(0).toUpperCase() + b.label.slice(1), color: b.color, meters, share: meters / total };
    }).filter((row) => row.meters >= MIN_LEGEND_M);
  }

  /** The distance (m) along the route under a pointer at `fraction` (0..1) of the plot's width. */
  function distanceAtFraction(profile, fraction) {
    return Math.max(0, Math.min(1, fraction)) * profile.totalM;
  }

  const api = {
    GRADE_BANDS,
    CLIMB_MIN_GAIN_M,
    CLIMB_MIN_LENGTH_M,
    haversine,
    vertices,
    smooth,
    build,
    steepest,
    climbs,
    at,
    bandOf,
    tickStepKm,
    chartSvg,
    bandPieces,
    bandLegend,
    distanceAtFraction,
  };
  root.BikeProfile = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
