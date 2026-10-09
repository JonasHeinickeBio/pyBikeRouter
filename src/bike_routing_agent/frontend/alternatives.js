/* Alternative routes as short cards (pure functions, no DOM access).
 *
 * The server decides what is a pro or a con (scoring/pros_cons.py: facts with
 * numbers, relative to the other distinct routes); this file only turns a
 * response into HTML. Tested under node (tests/test_frontend_alternatives.py).
 */
(function (root) {
  "use strict";

  // What each routing profile is called to a person (the profile name is a
  // detail of the engine). Unknown profiles fall back to the raw name.
  const STYLE_NAMES = {
    "custom_gravel-v2": "Gravel",
    "custom_gravel-v1": "Gravel (v1)",
    "custom_touring-v1": "Touring",
    "custom_commuter-v1": "Commuter",
    mtb: "Mountain bike",
    fastbike: "Fast road",
    trekking: "Trekking",
    "vm-forum-liegerad-schnell": "Recumbent",
    "cycling-regular": "Regular",
    "cycling-road": "Road",
    "cycling-mountain": "Mountain",
    "cycling-electric": "Electric",
    bicycle: "Standard",
  };

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** "Touring · brouter", "Regular · ors", or the raw profile when unknown. */
  function styleLabel(candidate) {
    const name = STYLE_NAMES[candidate.provider_profile] || candidate.provider_profile || "Route";
    return candidate.provider ? `${name} · ${candidate.provider}` : name;
  }

  function summaryLine(candidate) {
    const m = candidate.metrics || {};
    const bits = [];
    if (typeof m.distance_m === "number") bits.push(`${(m.distance_m / 1000).toFixed(1)} km`);
    if (typeof m.duration_s === "number") bits.push(`${Math.round(m.duration_s / 60)} min`);
    if (typeof m.ascent_m === "number") bits.push(`↑ ${Math.round(m.ascent_m)} m`);
    return bits.join(" · ");
  }

  function list(items, kind, sign) {
    if (!Array.isArray(items) || !items.length) return "";
    return (
      `<ul class="alt-${kind}">` +
      items.map((t) => `<li><span class="alt-sign" aria-hidden="true">${sign}</span>${escapeHtml(t)}</li>`).join("") +
      "</ul>"
    );
  }

  /** The distinct routes (indices into candidates), near-copies left out. */
  function distinctIndices(candidates) {
    const out = [];
    (candidates || []).forEach((c, i) => {
      if (!c.duplicate_of) out.push(i);
    });
    return out;
  }

  /** One card. `color` is the route's colour on the map, `selected` marks the recommended one. */
  function cardHtml(candidate, index, color, selected, active) {
    const classes = ["alt-card"];
    if (selected) classes.push("selected");
    if (active) classes.push("active");
    const tag = selected ? '<span class="alt-tag">Top ranked</span>' : "";
    const merged = (candidate.duplicates || []).length
      ? `<p class="alt-merged">${candidate.duplicates.length} near-identical route${candidate.duplicates.length === 1 ? "" : "s"} merged into this one</p>`
      : "";
    const pros = list(candidate.pros, "pros", "+");
    const cons = list(candidate.cons, "cons", "−");
    const none = !pros && !cons ? '<p class="alt-none">No notable difference from the others.</p>' : "";
    return (
      `<li><button type="button" class="${classes.join(" ")}" data-candidate="${index}" ` +
      `aria-pressed="${active ? "true" : "false"}" title="Show this route on the map">` +
      `<span class="alt-head"><span class="cand-dot" style="background:${escapeHtml(color)}"></span>` +
      `<span class="alt-title">${escapeHtml(styleLabel(candidate))}</span>${tag}</span>` +
      `<span class="alt-metrics">${escapeHtml(summaryLine(candidate))}</span>` +
      pros +
      cons +
      none +
      merged +
      "</button></li>"
    );
  }

  /** The whole "Alternatives" block, or "" with fewer than two distinct routes. */
  function alternativesHtml(candidates, colors, selectedIndex, activeIndex) {
    const distinct = distinctIndices(candidates);
    if (distinct.length < 2) return "";
    return (
      '<ol class="alt-list">' +
      distinct
        .map((i) => cardHtml(candidates[i], i, (colors && colors[i]) || "#888", i === selectedIndex, i === activeIndex))
        .join("") +
      "</ol>"
    );
  }

  const api = { styleLabel, summaryLine, distinctIndices, cardHtml, alternativesHtml, escapeHtml };
  root.BikeAlternatives = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
