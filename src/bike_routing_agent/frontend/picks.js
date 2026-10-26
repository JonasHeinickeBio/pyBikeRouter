/* Which route a person takes when several were compared (pure functions, no DOM access).
 *
 * With `PICK_LOG_PATH` set on the server, the page reports a download of one of the compared routes
 * to `POST /v1/route/pick`, which keeps it in a file on the same machine (optimizer/picklog.py).
 * This module builds that report: only the numbers of the distinct routes (their `metrics`, never
 * the geometry, the places or the cue text), the position of the route taken and of the one the
 * optimiser recommended, the goals the plan was asked for and the bike type.
 * Tested under node (tests/test_frontend_picks.py).
 */
(function (root) {
  "use strict";

  const MAX_CANDIDATES = 16; // the server's limit

  function finite(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  /**
   * The report for downloading `candidates[activeIndex]` when `selectedIndex` is the recommended
   * one, or null when there is nothing to report (fewer than two distinct routes, the route taken
   * is a near-copy that was not offered, or too many routes for the server).
   * `goals` is the optimisation report's goal list (`[{metric, weight}]`); unusable entries are left out.
   */
  function payload(candidates, activeIndex, selectedIndex, bikeType, goals) {
    if (!Array.isArray(candidates)) return null;
    const distinct = [];
    candidates.forEach((c, i) => {
      if (c && typeof c === "object" && !c.duplicate_of && c.metrics && typeof c.metrics === "object") distinct.push(i);
    });
    if (distinct.length < 2 || distinct.length > MAX_CANDIDATES) return null;
    const picked = distinct.indexOf(activeIndex);
    const selected = distinct.indexOf(selectedIndex);
    if (picked === -1 || selected === -1) return null;
    const body = {
      candidates: distinct.map((i) => ({ metrics: candidates[i].metrics })),
      picked,
      selected,
      goals: (Array.isArray(goals) ? goals : [])
        .filter((g) => g && typeof g.metric === "string" && finite(g.weight) && g.weight > 0 && g.weight <= 10)
        .map((g) => ({ metric: g.metric, weight: g.weight })),
    };
    if (typeof bikeType === "string" && bikeType) body.bike_type = bikeType.slice(0, 40);
    return body;
  }

  /** The same pick again (a second file format of the same route) has the same key. */
  function key(body) {
    return JSON.stringify([body.picked, body.selected, body.candidates.map((c) => c.metrics.distance_m)]);
  }

  const api = { payload, key, MAX_CANDIDATES };
  root.BikePicks = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
