/* Helpers for planning from plain words (pure functions, no DOM access).
 *
 * The server returns `interpretation`: the structured request it read the text
 * as. These helpers turn that into form values (so the person can see, correct
 * and re-plan it with the normal controls) and into a short readable summary.
 * Tested under node (tests/test_frontend_text_planning.py).
 */
(function (root) {
  "use strict";

  const BIKE_LABELS = {
    road: "road bike",
    gravel: "gravel bike",
    touring: "touring bike",
    mountain: "mountain bike",
    city: "city bike",
    ebike: "e-bike",
    commuter: "commuter bike",
    recumbent: "recumbent",
  };

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  /** An ISO instant as the "YYYY-MM-DDTHH:mm" a datetime-local input wants (viewer's local time). */
  function toLocalInputValue(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
      `T${pad(d.getHours())}:${pad(d.getMinutes())}`
    );
  }

  /** The form fields an interpretation fills in. Unstated constraints stay untouched (null). */
  function formValues(interpretation) {
    if (!interpretation || !interpretation.request) return null;
    const request = interpretation.request;
    const c = request.constraints || {};
    const list = (v) => (Array.isArray(v) ? v.join(", ") : "");
    const nullable = (v) => (v === undefined || v === null ? null : v);
    return {
      origin: request.origin || "",
      destination: request.destination || "",
      via: Array.isArray(request.via) ? request.via.slice() : [],
      bikeType: nullable(c.bike_type),
      targetDistance: nullable(c.target_distance_km),
      maxDistance: nullable(c.max_distance_km),
      maxAscent: nullable(c.max_ascent_m),
      prefer: list(c.prefer_surfaces),
      avoid: list(c.avoid_surfaces),
      avoidTraffic: nullable(c.avoid_high_traffic_roads),
      avoidFerries: nullable(c.avoid_ferries),
      loop: nullable(c.return_to_origin),
      departureLocal: interpretation.departure_time
        ? toLocalInputValue(interpretation.departure_time)
        : null,
    };
  }

  /** One readable sentence-ish summary of what was understood, as HTML. */
  function summaryHtml(interpretation) {
    const values = formValues(interpretation);
    if (!values) return "";
    const request = interpretation.request;
    const c = request.constraints || {};
    const bits = [];
    if (values.loop) {
      bits.push(`loop from <strong>${escapeHtml(values.origin)}</strong>`);
    } else {
      bits.push(
        `<strong>${escapeHtml(values.origin)}</strong> &rarr; <strong>${escapeHtml(values.destination || "?")}</strong>`,
      );
    }
    if (values.via.length) bits.push(`via ${values.via.map(escapeHtml).join(", ")}`);
    if (values.bikeType) bits.push(BIKE_LABELS[values.bikeType] || escapeHtml(values.bikeType));
    if (values.targetDistance !== null) bits.push(`about ${values.targetDistance} km`);
    if (values.maxDistance !== null) bits.push(`at most ${values.maxDistance} km`);
    if (values.maxAscent !== null) bits.push(`max ${values.maxAscent} m climbing`);
    if (c.loop_direction) {
      bits.push(c.loop_direction === "clockwise" ? "clockwise" : "counter-clockwise");
    }
    if (values.prefer) bits.push(`prefers ${escapeHtml(values.prefer)}`);
    if (values.avoid) bits.push(`avoids ${escapeHtml(values.avoid)}`);
    if (values.avoidFerries === false) bits.push("ferries are fine");
    if (values.avoidTraffic === false) bits.push("busy roads are fine");
    let html = `Understood: ${bits.join(" &middot; ")}.`;
    const notes = (interpretation.notes || []).filter((n) => typeof n === "string" && n.trim());
    if (notes.length) {
      html +=
        '<ul class="interpretation-notes">' +
        notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("") +
        "</ul>";
    }
    return html;
  }

  const api = { formValues, summaryHtml, toLocalInputValue, escapeHtml };
  root.BikeText = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
