/* "Remember my choices": an opt-in memory of the plan form in this browser (pure functions).
 *
 * Off until the person switches it on. They choose *what* is remembered -- the bike type, surface
 * preferences, busy roads and ferries, distance and climbing limits, the start place -- and can
 * forget it all. It lives in this browser's storage only; nothing is sent anywhere, and a link
 * (`#plan=...`) or a restored chat always wins over it. What is read back is validated like any
 * other text from outside (a stored value can be edited by hand or left by an older version):
 * only the known fields, of the right type and in range, ever reach the form.
 * Tested under node (tests/test_frontend_memory.py).
 */
(function (root) {
  "use strict";

  const KEY = "pybikerouter.memory.v1";
  const BIKE_TYPES = ["road", "gravel", "touring", "mountain", "city", "ebike", "commuter", "recumbent"];
  const MAX_WORD = 40;
  const MAX_WORDS = 10;
  const MAX_PLACE = 200;

  /** What can be remembered, in the order the settings list them. */
  const KINDS = [
    { key: "bike", label: "Bike type", fields: ["bike_type"] },
    { key: "surfaces", label: "Surface preferences", fields: ["prefer_surfaces", "avoid_surfaces"] },
    { key: "roads", label: "Busy roads and ferries", fields: ["avoid_high_traffic_roads", "avoid_ferries"] },
    { key: "limits", label: "Distance and climbing limits", fields: ["target_distance_km", "max_distance_km", "max_ascent_m"] },
    { key: "start", label: "Start place", fields: ["origin"] },
  ];
  // What is ticked when memory is first switched on: the habits of a rider, not the trip of the day.
  const DEFAULT_KEEP = { bike: true, surfaces: true, roads: true, limits: false, start: false };

  const NUMBER_RANGES = { target_distance_km: [0, 2000], max_distance_km: [0, 2000], max_ascent_m: [0, 20000] };
  const BOOLEANS = ["avoid_high_traffic_roads", "avoid_ferries"];
  const WORD_LISTS = ["prefer_surfaces", "avoid_surfaces"];

  function emptyState() {
    return { enabled: false, keep: { ...DEFAULT_KEEP }, values: {} };
  }

  /** One stored/entered field, checked: its value, or `undefined` when it is not usable. */
  function cleanField(name, value) {
    if (name === "bike_type") return BIKE_TYPES.includes(value) ? value : undefined;
    if (name in NUMBER_RANGES) {
      const [low, high] = NUMBER_RANGES[name];
      return typeof value === "number" && Number.isFinite(value) && value >= low && value <= high ? value : undefined;
    }
    if (BOOLEANS.includes(name)) return typeof value === "boolean" ? value : undefined;
    if (WORD_LISTS.includes(name)) {
      if (!Array.isArray(value)) return undefined;
      return value
        .filter((w) => typeof w === "string" && w.trim() && w.length <= MAX_WORD)
        .slice(0, MAX_WORDS)
        .map((w) => w.trim());
    }
    if (name === "origin") {
      return typeof value === "string" && value.trim() && value.length <= MAX_PLACE ? value.trim() : undefined;
    }
    return undefined;
  }

  function cleanValues(raw, keep) {
    const values = {};
    if (!raw || typeof raw !== "object") return values;
    for (const kind of KINDS) {
      if (!keep[kind.key]) continue; // a field that is not to be remembered is dropped, not trusted
      for (const field of kind.fields) {
        const clean = cleanField(field, raw[field]);
        if (clean !== undefined) values[field] = clean;
      }
    }
    return values;
  }

  function cleanKeep(raw) {
    const keep = { ...DEFAULT_KEEP };
    if (raw && typeof raw === "object") {
      for (const kind of KINDS) if (typeof raw[kind.key] === "boolean") keep[kind.key] = raw[kind.key];
    }
    return keep;
  }

  /** The stored state, validated; a switched-off, empty one when there is none or it is unreadable. */
  function load(storage) {
    try {
      const raw = JSON.parse(storage.getItem(KEY));
      if (!raw || typeof raw !== "object" || raw.enabled !== true) return emptyState();
      const keep = cleanKeep(raw.keep);
      return { enabled: true, keep, values: cleanValues(raw.values, keep) };
    } catch {
      return emptyState();
    }
  }

  /** Store the state; false when the browser would not (private window, blocked storage). */
  function save(storage, state) {
    try {
      storage.setItem(KEY, JSON.stringify({ enabled: state.enabled, keep: state.keep, values: state.values }));
      return true;
    } catch {
      return false;
    }
  }

  /** Remove everything this module ever stored. */
  function erase(storage) {
    try {
      storage.removeItem(KEY);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * The state after the form was filled in with `form` (the same field names as the plan request's
   * constraints, plus `origin`): only the kinds that are ticked are kept, and a kind whose fields
   * are all empty forgets what it held. Does nothing while memory is off.
   */
  function remember(state, form) {
    if (!state.enabled) return state;
    const values = cleanValues(form, state.keep);
    return { ...state, values };
  }

  /** The values to put in the form: ticked kinds only, nothing while memory is off. */
  function recall(state) {
    return state.enabled ? cleanValues(state.values, state.keep) : {};
  }

  /** A sentence saying what is remembered right now, for the settings panel. */
  function describe(state) {
    if (!state.enabled) return "Off: nothing is remembered.";
    const held = KINDS.filter((k) => state.keep[k.key] && k.fields.some((f) => f in state.values));
    if (!held.length) return "On: nothing stored yet (it is saved as you fill in the form).";
    return `Remembering: ${held.map((k) => k.label.toLowerCase()).join(", ")}.`;
  }

  /** The state with one kind ticked or unticked (what it held is dropped when unticked). */
  function setKept(state, kindKey, kept) {
    if (!KINDS.some((k) => k.key === kindKey)) return state;
    const keep = { ...state.keep, [kindKey]: Boolean(kept) };
    return { ...state, keep, values: cleanValues(state.values, keep) };
  }

  /** The state with memory switched on or off (off forgets the values, the choices stay). */
  function setEnabled(state, enabled) {
    return enabled ? { ...state, enabled: true } : { ...state, enabled: false, values: {} };
  }

  const api = { KEY, KINDS, DEFAULT_KEEP, emptyState, load, save, erase, remember, recall, describe, setKept, setEnabled };
  root.BikeMemory = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
