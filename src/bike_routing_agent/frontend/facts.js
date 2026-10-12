/* Traffic and surface of a route as short facts (pure functions, no DOM access).
 *
 * The same facts, worded the same way, as bike_routing_agent/route_facts.py:
 * - traffic is `main_road_share`: the share of the route on trunk/primary/secondary/tertiary
 *   roads without a mapped cycle lane or track -- a proxy for exposure to motor traffic from
 *   the road tags, not a measurement; null when the engine gave no tags;
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

  /** "19 % on main roads without a bike lane", or null when the engine gave no tags. */
  function trafficText(metrics) {
    const share = (metrics || {}).main_road_share;
    return share == null ? null : `${pct(share)} on main roads without a bike lane`;
  }

  const api = { surfaceShares, surfaceText, pavedShare, trafficText, pct };
  root.BikeFacts = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
