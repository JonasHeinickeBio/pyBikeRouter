/* Traffic and surface of a route as short facts (pure functions, no DOM access).
 *
 * The same facts, worded the same way, as bike_routing_agent/route_facts.py:
 * - traffic is `traffic_shares`: length shares in four bands (busy roads / some traffic / quiet
 *   streets / paths and tracks), estimated from road class, cycle infrastructure, speed limit
 *   and access -- an estimate from what is mapped, not a count of cars; without it
 *   `main_road_share` (main roads without a bike lane); null when the engine gave no tags;
 * - surface is `engine_surface_shares` (paved / unpaved / cobbles / unknown), or else the
 *   Overpass enrichment (`surface_coverage` + `unknown_surface_fraction`). "Unknown" stays
 *   unknown: it is never counted as paved.
 */
(function (root) {
  "use strict";

  const pct = (share) => `${Math.round(share * 100)} %`;

  /** [[surface, share], ...], largest first, zero shares left out. */
  function surfaceShares(metrics) {
    const m = metrics || {};
    let shares = Object.assign({}, m.engine_surface_shares || {});
    if (!Object.keys(shares).length) {
      shares = Object.assign({}, m.surface_coverage || {});
      if (Object.keys(shares).length && m.unknown_surface_fraction) {
        shares.unknown = m.unknown_surface_fraction;
      }
    }
    return Object.entries(shares)
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1]);
  }

  /** "71 % paved, 24 % unpaved, 5 % unknown", or null without data. */
  function surfaceText(metrics) {
    const shares = surfaceShares(metrics);
    return shares.length ? shares.map(([k, v]) => `${pct(v)} ${k}`).join(", ") : null;
  }

  /** The paved share (0..1) or null. */
  function pavedShare(metrics) {
    const found = surfaceShares(metrics).find(([k]) => k === "paved");
    return found ? found[1] : null;
  }

  const TRAFFIC_LABELS = [
    ["busy", "busy roads"],
    ["some", "some traffic"],
    ["quiet", "quiet streets"],
    ["off_road", "paths and tracks"],
  ];

  /** "5 % busy roads, 12 % some traffic, ..." (the estimate by band); without it
   * "19 % on main roads without a bike lane"; null when the engine gave no tags. */
  function trafficText(metrics) {
    const m = metrics || {};
    const shares = m.traffic_shares || {};
    if (Object.keys(shares).length) {
      const shown = TRAFFIC_LABELS.filter(([band]) => (shares[band] || 0) >= 0.005).map(
        ([band, label]) => `${pct(shares[band])} ${label}`
      );
      if (shown.length) return shown.join(", ");
    }
    return m.main_road_share == null
      ? null
      : `${pct(m.main_road_share)} on main roads without a bike lane`;
  }

  /** The busy-road share (0..1): the band estimate when there is one, else the main-road
   * share; null without tags. */
  function busyShare(metrics) {
    const m = metrics || {};
    const shares = m.traffic_shares || {};
    if (Object.keys(shares).length) return shares.busy || 0;
    return m.main_road_share == null ? null : m.main_road_share;
  }

  const api = { surfaceShares, surfaceText, pavedShare, trafficText, busyShare, pct };
  root.BikeFacts = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
