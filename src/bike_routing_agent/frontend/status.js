/* The service status indicator (pure functions, no DOM access): turns the answer of GET /readyz
 * into a level, a sentence and a list of services. Tested under node (tests/test_frontend_status.py).
 *
 * The server says, per service, ok / degraded / unavailable / unknown; "unknown" is a service with
 * no health signal (the public openrouteservice), which is no evidence of a problem.
 */
(function (root) {
  "use strict";

  const LEVELS = ["ok", "degraded", "unavailable"];

  // What a person calls each kind of service.
  const KIND_LABELS = {
    routing: "Route engine",
    geocoder: "Place search",
    weather: "Weather",
    pois: "Sights",
    artifact_store: "Route files",
    database: "History",
    cache: "Cache",
  };

  const STATE_WORDS = {
    ok: "up",
    degraded: "slow or partly down",
    unavailable: "down",
    unknown: "no signal",
  };

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** The label of a service: its kind, plus its own name when there are several of that kind. */
  function labelOf(component, siblings) {
    const base = KIND_LABELS[component.kind] || String(component.kind || "Service");
    const same = (siblings || []).filter((c) => c.kind === component.kind).length;
    return same > 1 || component.kind === "routing" || component.kind === "geocoder"
      ? `${base} (${component.name})`
      : base;
  }

  /** "ok" | "degraded" | "unavailable" for a /readyz answer, or null when it is not one. */
  function levelOf(report) {
    return report && LEVELS.includes(report.status) ? report.status : null;
  }

  /** The services that are not fine: degraded or down (a service with no signal is not listed). */
  function problems(report) {
    const list = (report && Array.isArray(report.components) ? report.components : []).filter(
      (c) => c && (c.status === "degraded" || c.status === "unavailable"),
    );
    return list.map((c) => ({ label: labelOf(c, report.components), status: c.status }));
  }

  /** One sentence for the indicator. */
  function summaryText(report) {
    const level = levelOf(report);
    if (level === null) return "Status unknown";
    if (level === "ok") return "All services up";
    const names = problems(report).map((p) => p.label);
    if (level === "unavailable") return "Cannot plan routes right now";
    return names.length ? `Some services are down: ${names.join(", ")}` : "Some services are down";
  }

  /** The list of services as HTML: a state each, with the server's short reason when it gave one. */
  function rowsHtml(report) {
    const components = report && Array.isArray(report.components) ? report.components : [];
    return components
      .filter((c) => c && typeof c.name === "string")
      .map((c) => {
        const state = STATE_WORDS[c.status] ? c.status : "unknown";
        const reason = c.detail && state !== "ok" ? ` &middot; ${escapeHtml(c.detail)}` : "";
        return (
          `<li class="svc ${state}"><span class="svc-dot" aria-hidden="true"></span>` +
          `<span class="svc-name">${escapeHtml(labelOf(c, components))}</span>` +
          `<span class="svc-state">${escapeHtml(STATE_WORDS[state])}${reason}</span></li>`
        );
      })
      .join("");
  }

  const api = { LEVELS, KIND_LABELS, levelOf, problems, summaryText, rowsHtml, labelOf, escapeHtml };
  root.BikeStatus = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
