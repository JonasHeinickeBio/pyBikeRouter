/* Fine-tuning a ride for the rider (pure helpers, no DOM access).
 *
 * The settings a routing profile takes come from GET /v1/profiles/settings (one entry per
 * setting: name, boolean or number with a range, what it does, whether it changes the route or
 * only the time estimate). This file groups them for display, reads what the person typed into
 * the `profile_params` of a plan request, and drops what the next bike type's profile does not
 * have. Tested under node (tests/test_frontend_rider_settings.py).
 *
 * A field left empty means "the profile's own default": nothing is sent for it.
 */
(function (root) {
  "use strict";

  /** The order and the headings of the groups the API names. */
  const GROUPS = [
    { key: "preferences", label: "Avoid and prefer" },
    { key: "cycle routes", label: "Cycle routes" },
    { key: "climbing", label: "Climbing" },
    { key: "rider", label: "Rider and bike (changes the time estimate, not the route)" },
  ];

  /** The settings, grouped for display in a fixed order; unknown groups come last. */
  function groups(settings) {
    const byKey = new Map();
    for (const s of settings || []) {
      if (!byKey.has(s.group)) byKey.set(s.group, []);
      byKey.get(s.group).push(s);
    }
    const known = GROUPS.filter((g) => byKey.has(g.key)).map((g) => ({ key: g.key, label: g.label, items: byKey.get(g.key) }));
    const extra = [...byKey.keys()].filter((k) => !GROUPS.some((g) => g.key === k)).map((k) => ({ key: k, label: k, items: byKey.get(k) }));
    return [...known, ...extra];
  }

  /** The hint under a field: what it does and the range it takes. */
  function hint(setting) {
    const range = setting.type === "boolean" ? "on/off" : `${setting.minimum}–${setting.maximum}`;
    return `${setting.meaning} (${range})`;
  }

  /** One typed value as the request wants it, or undefined to send nothing for it. */
  function parseValue(setting, raw) {
    const text = raw === null || raw === undefined ? "" : String(raw).trim();
    if (text === "") return undefined;
    if (setting.type === "boolean") {
      if (text === "on") return true;
      if (text === "off") return false;
      return undefined;
    }
    const number = Number(text);
    return Number.isFinite(number) ? number : undefined;
  }

  /** What to send as `profile_params`: the typed values of the settings the profile has. */
  function readParams(settings, raw) {
    const params = {};
    for (const s of settings || []) {
      const value = parseValue(s, raw ? raw[s.name] : undefined);
      if (value !== undefined) params[s.name] = value;
    }
    return params;
  }

  /** The typed values that still belong to a profile (after the bike type changed). */
  function keep(raw, settings) {
    const names = new Set((settings || []).map((s) => s.name));
    const kept = {};
    for (const [name, value] of Object.entries(raw || {})) {
      if (names.has(name)) kept[name] = value;
    }
    return kept;
  }

  /** "2 changed" for the summary line; empty when nothing is set. */
  function summary(params) {
    const n = Object.keys(params || {}).length;
    return n === 0 ? "" : `${n} changed`;
  }

  const api = { GROUPS, groups, hint, parseValue, readParams, keep, summary };
  root.BikeRiderSettings = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
