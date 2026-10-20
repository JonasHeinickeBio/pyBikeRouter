/* Adding a point picked on the map to the trip (pure functions, no DOM access).
 *
 * A click on the map selects a point; this module says where in the trip it can go -- as the start,
 * as the end, or as a via stop at any position between them -- which of those fits best, and what
 * the places look like afterwards. The page (app.js) only draws the menu and edits the form.
 * Tested under node (tests/test_frontend_waypoints.py).
 */
(function (root) {
  "use strict";

  const MAX_VIA = 10; // the server's limit (MAX_VIA_POINTS)
  const pois =
    root.BikePois || (typeof require !== "undefined" ? require("./pois.js") : null);

  /** A longitude brought into -180..180: a map panned across the date line reports 190, -200 ... */
  function wrapLon(lon) {
    return ((((lon + 180) % 360) + 360) % 360) - 180;
  }

  /** "lat, lon" as the form's place fields read it. Latitude is kept in range, longitude wrapped. */
  function coordText(lat, lon) {
    const la = Math.max(-90, Math.min(90, lat));
    return `${la.toFixed(5)}, ${wrapLon(lon).toFixed(5)}`;
  }

  /**
   * Where a point can go, in route order: the start, a via stop at each position, the end.
   * `viaCount` stops are in the trip now, so a new one fits at positions 0..viaCount. Each option is
   * `{ key, label }`; keys are "start", "end" and "via:<index the new stop will have>".
   */
  function slots(viaCount, { hasOrigin = true, hasDestination = true } = {}) {
    const n = Math.max(0, viaCount | 0);
    const out = [{ key: "start", label: hasOrigin ? "Start (A) – replaces the current one" : "Start (A)" }];
    if (n < MAX_VIA) {
      for (let k = 0; k <= n; k++) {
        const after = k === 0 ? "after the start" : `after stop ${k}`;
        const before = k === n ? (hasDestination ? ", before the end" : "") : "";
        out.push({ key: `via:${k}`, label: `Stop ${k + 1} – ${after}${before}` });
      }
    }
    out.push({ key: "end", label: hasDestination ? "End (B) – replaces the current one" : "End (B)" });
    return out;
  }

  /**
   * The option that fits best. With no start yet the point is the start; with a start but no end it
   * is the end. Otherwise it is a stop at the position along the trip nearest to it: along the route
   * on the map when there is one, else along the straight lines between the places. `route` is a
   * list of [lon, lat]; `origin`, `destination`, `vias` and `point` are { lat, lon } (null for a
   * place that is only a name, which cannot be placed -- the stop then goes last).
   */
  function suggest({ hasOrigin, hasDestination, origin, destination, vias, route, point }) {
    if (!hasOrigin) return "start";
    if (!hasDestination) return "end";
    const list = vias || [];
    if (list.length >= MAX_VIA) return "end";
    let line = route && route.length >= 2 ? route : null;
    if (!line && origin && destination && list.every((v) => v)) {
      line = [origin, ...list, destination].map((p) => [p.lon, p.lat]);
    }
    const index = pois && line ? pois.insertionIndex(line, list, point) : list.length;
    return `via:${index}`;
  }

  /**
   * The places after the point is added at `key`: `{ origin, destination, vias, label }` (the
   * strings the form holds; `vias` as a new list). `label` names what the point became.
   */
  function place({ origin, destination, vias }, key, text) {
    const list = (vias || []).slice();
    if (key === "start") return { origin: text, destination, vias: list, label: "the start (A)" };
    if (key === "end") return { origin, destination: text, vias: list, label: "the end (B)" };
    const match = /^via:(\d+)$/.exec(key);
    if (!match || list.length >= MAX_VIA) return null;
    const index = Math.min(Number(match[1]), list.length);
    list.splice(index, 0, text);
    return { origin, destination, vias: list, label: `stop ${index + 1}` };
  }

  const api = { MAX_VIA, wrapLon, coordText, slots, suggest, place };
  root.BikeWaypoints = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
