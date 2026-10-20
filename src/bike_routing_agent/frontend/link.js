/* A link that opens the planner with a trip already in the form (pure functions, no DOM).
 *
 * The trip is the plan form's request (places, via stops, constraints, alternatives, departure)
 * as JSON in the URL *fragment*: `#plan=<base64url>`. A fragment never reaches a server, so the
 * places in it are not in anyone's logs. Reading a link is strict, because a link is text from
 * somewhere else: only the known fields, of the right types and in sane ranges, ever reach the
 * form; anything else is dropped, and a link that carries no usable origin and destination is
 * ignored altogether. A departure that has passed (or is beyond the forecast) is dropped.
 * Tested under node (tests/test_frontend_link.py).
 */
(function (root) {
  "use strict";

  const KEY = "plan";
  const MAX_ENCODED = 3000; // longer than this is not a link anyone can paste anywhere
  const MAX_VIA = 10;
  const MAX_TEXT = 200;
  const MAX_FORECAST_MS = 14 * 86400 * 1000;
  const BIKE_TYPES = ["road", "gravel", "touring", "mountain", "city", "ebike", "commuter", "recumbent"];
  const NUMBER_FIELDS = { target_distance_km: [0, 2000], max_distance_km: [0, 2000], max_ascent_m: [0, 20000] };
  const BOOL_FIELDS = ["avoid_high_traffic_roads", "avoid_ferries", "return_to_origin"];
  const LIST_FIELDS = ["prefer_surfaces", "avoid_surfaces"];

  function toBase64Url(text) {
    const bytes = new TextEncoder().encode(text);
    let binary = "";
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function fromBase64Url(text) {
    const padded = text.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
    return new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(binary, (c) => c.charCodeAt(0)));
  }

  function place(value) {
    if (typeof value === "string") {
      const text = value.trim();
      return text && text.length <= MAX_TEXT ? text : null;
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const { lat, lon } = value;
      const ok = (n, limit) => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= limit;
      return ok(lat, 90) && ok(lon, 180) ? { lat, lon } : null;
    }
    return null;
  }

  function constraints(raw) {
    const out = {};
    if (!raw || typeof raw !== "object") return out;
    if (BIKE_TYPES.includes(raw.bike_type)) out.bike_type = raw.bike_type;
    for (const [key, [low, high]] of Object.entries(NUMBER_FIELDS)) {
      const v = raw[key];
      if (typeof v === "number" && Number.isFinite(v) && v >= low && v <= high) out[key] = v;
    }
    for (const key of BOOL_FIELDS) if (typeof raw[key] === "boolean") out[key] = raw[key];
    for (const key of LIST_FIELDS) {
      if (Array.isArray(raw[key])) {
        out[key] = raw[key].filter((s) => typeof s === "string" && s.trim() && s.length <= 40).slice(0, 10).map((s) => s.trim());
      }
    }
    return out;
  }

  /** The request reduced to what a link carries, or null when it has no origin and destination. */
  function clean(request, nowMs) {
    if (!request || typeof request !== "object") return null;
    const origin = place(request.origin);
    const destination = place(request.destination);
    if (origin === null || destination === null) return null;
    const out = {
      origin,
      destination,
      via: (Array.isArray(request.via) ? request.via : []).map(place).filter((p) => p !== null).slice(0, MAX_VIA),
      constraints: constraints(request.constraints),
    };
    const alternatives = request.max_alternatives;
    if (Number.isInteger(alternatives) && alternatives >= 1 && alternatives <= 5) out.max_alternatives = alternatives;
    if (typeof request.departure_time === "string") {
      const when = Date.parse(request.departure_time);
      if (Number.isFinite(when) && when > nowMs && when <= nowMs + MAX_FORECAST_MS) {
        out.departure_time = new Date(when).toISOString();
      }
    }
    return out;
  }

  /** The fragment (`#plan=...`) for a request, or null when it cannot be a link. */
  function encode(request, nowMs) {
    const trimmed = clean(request, nowMs === undefined ? Date.now() : nowMs);
    if (!trimmed) return null;
    const payload = toBase64Url(JSON.stringify(trimmed));
    return payload.length > MAX_ENCODED ? null : `#${KEY}=${payload}`;
  }

  /** The request in a fragment, cleaned, or null when there is none or it is not usable. */
  function decode(fragment, nowMs) {
    if (typeof fragment !== "string") return null;
    const match = new RegExp(`^#${KEY}=([A-Za-z0-9_-]{1,${MAX_ENCODED}})$`).exec(fragment);
    if (!match) return null;
    try {
      return clean(JSON.parse(fromBase64Url(match[1])), nowMs === undefined ? Date.now() : nowMs);
    } catch {
      return null; // not base64, not UTF-8 or not JSON
    }
  }

  const api = { encode, decode, KEY };
  root.BikeLink = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
