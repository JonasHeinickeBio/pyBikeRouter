/* Progress while a plan is made, and where its time went (pure functions, no DOM access).
 *
 * `POST /v1/route/plan/stream` tells a plan as server-sent events: a `step` after every stage of the
 * planner, a `route` as soon as the best route exists (before the forecast, the alternatives' notes
 * and the files are done), one `result` (the body `/v1/route/plan?trace=true` would return) or an
 * `error`. This module reads that stream in pieces as the network delivers it, words the progress,
 * turns the early route into something to draw and lays the trace out as "where the time went".
 * Everything that comes from the server is treated as text from outside: labels are looked up in a
 * fixed table (an unknown stage is shown by its escaped name) and numbers are checked.
 * Tested under node (tests/test_frontend_stream.py).
 */
(function (root) {
  "use strict";

  // The stages of a plan, in order (tracing.STEP_LABELS on the server; a test keeps them equal).
  const STEPS = [
    ["parse_request", "Reading the request"],
    ["validate_request", "Checking the request"],
    ["geocode_locations", "Finding the places"],
    ["select_poi_stops", "Choosing sights"],
    ["route_with_provider", "Routing"],
    ["enrich_candidates", "Reading the road data"],
    ["score_candidates", "Scoring the routes"],
    ["optimize_candidates", "Comparing the options"],
    ["weather_candidates", "Fetching the forecast"],
    ["annotate_alternatives", "Comparing the alternatives"],
    ["explain_and_export", "Writing the files"],
  ];
  const LABELS = Object.fromEntries(STEPS);
  const KNOWN_EVENTS = ["step", "route", "result", "error"];
  const MAX_EVENT_CHARS = 4 * 1024 * 1024; // a result with several long routes is a few hundred KB

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  /**
   * A reader for an event stream that arrives in arbitrary pieces. `push(text)` returns the events
   * completed by that piece (`{event, data}`; `data` already parsed); an event cut in two waits for
   * its rest. Unknown events, comments and events whose data is not JSON are skipped, never thrown.
   */
  function createParser() {
    let buffer = "";
    function parseBlock(block) {
      let event = "message";
      const data = [];
      for (const raw of block.split("\n")) {
        const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
        if (line === "" || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        let value = colon === -1 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
      }
      if (!KNOWN_EVENTS.includes(event) || !data.length) return null;
      try {
        return { event, data: JSON.parse(data.join("\n")) };
      } catch {
        return null;
      }
    }
    return {
      push(text) {
        buffer += text;
        const events = [];
        let cut;
        while ((cut = buffer.search(/\r?\n\r?\n/)) !== -1) {
          const match = buffer.slice(cut).match(/^\r?\n\r?\n/);
          const parsed = parseBlock(buffer.slice(0, cut));
          buffer = buffer.slice(cut + match[0].length);
          if (parsed) events.push(parsed);
        }
        if (buffer.length > MAX_EVENT_CHARS) buffer = ""; // not an event stream: do not grow forever
        return events;
      },
    };
  }

  /** The words for a step being done: "Routing… (step 5 of 11)". */
  function progressText(step) {
    if (!step || typeof step !== "object") return "Planning route…";
    const label = typeof step.label === "string" && step.label ? step.label : LABELS[step.node];
    if (!label) return "Planning route…";
    const index = STEPS.findIndex(([node]) => node === step.node);
    return index === -1 ? `${label}…` : `${label}… (step ${index + 1} of ${STEPS.length})`;
  }

  /** The early route as `[lat, lon]` pairs to draw, or null when it is not a usable line. */
  function provisionalLine(route) {
    const geometry = route && route.geometry_geojson;
    if (!geometry || geometry.type !== "LineString" || !Array.isArray(geometry.coordinates)) return null;
    const points = [];
    for (const c of geometry.coordinates) {
      if (!Array.isArray(c) || c.length < 2) continue;
      const [lon, lat] = c;
      if (typeof lat !== "number" || typeof lon !== "number" || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) continue;
      points.push([lat, lon]);
    }
    return points.length >= 2 ? points : null;
  }

  /**
   * Where the time of a plan went: the steps in the order they ran, each with its time, its share of
   * the total and a bar width relative to the slowest step; `null` when there is no usable trace.
   */
  function timeBreakdown(trace) {
    if (!Array.isArray(trace)) return null;
    const steps = trace
      .filter((s) => s && typeof s === "object" && typeof s.node === "string" && Number.isFinite(s.ms) && s.ms >= 0)
      .map((s) => ({ node: s.node, label: LABELS[s.node] || s.node, ms: s.ms, ok: s.ok !== false }));
    if (!steps.length) return null;
    const total = steps.reduce((sum, s) => sum + s.ms, 0);
    const slowest = steps.reduce((m, s) => Math.max(m, s.ms), 0);
    let slowestNode = null;
    for (const s of steps) if (s.ms === slowest && slowestNode === null) slowestNode = s.node;
    return {
      total_ms: total,
      slowest: slowestNode,
      rows: steps.map((s) => ({
        ...s,
        share: total > 0 ? s.ms / total : 0,
        width: slowest > 0 ? s.ms / slowest : 0,
        slowest: s.node === slowestNode,
      })),
    };
  }

  /** A time for a person: "820 ms", "4.2 s", "2 min 5 s". */
  function formatMs(ms) {
    if (!Number.isFinite(ms) || ms < 0) return "";
    if (ms < 1000) return `${Math.round(ms)} ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
    const minutes = Math.floor(ms / 60000);
    return `${minutes} min ${Math.round((ms - minutes * 60000) / 1000)} s`;
  }

  /** The "where the time went" list as HTML (every piece of text escaped). */
  function breakdownHtml(trace, served) {
    const view = timeBreakdown(trace);
    if (!view) return served ? '<p class="hint">Served from the short-lived cache: nothing was computed.</p>' : "";
    const rows = view.rows
      .map(
        (r) =>
          `<li class="${r.slowest ? "slowest" : ""}${r.ok ? "" : " failed"}">` +
          `<span class="trace-label">${escapeHtml(r.label)}</span>` +
          `<span class="trace-bar" aria-hidden="true"><span style="width:${Math.max(1, Math.round(r.width * 100))}%"></span></span>` +
          `<span class="trace-ms">${escapeHtml(formatMs(r.ms))}</span></li>`
      )
      .join("");
    return `<p class="hint">${escapeHtml(formatMs(view.total_ms))} in total; the slowest step is highlighted.</p><ol class="trace-list">${rows}</ol>`;
  }

  /** One line for the card's summary: "5.2 s, mostly Routing". */
  function summaryText(trace) {
    const view = timeBreakdown(trace);
    if (!view) return "";
    const top = view.rows.find((r) => r.slowest);
    return `${formatMs(view.total_ms)}, mostly ${top ? top.label.toLowerCase() : "?"}`;
  }

  const api = { STEPS, LABELS, createParser, progressText, provisionalLine, timeBreakdown, formatMs, breakdownHtml, summaryText };
  root.BikeStream = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
