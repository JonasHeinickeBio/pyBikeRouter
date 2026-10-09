/* "BRouter has no map data here": put the choice to the user (pure functions, no DOM access).
 *
 * The server reports a coverage gap as an error with code `routing_area_not_covered`
 * (providers/brouter_segments.py, nodes/route.py). The app never downloads anything or
 * changes its own settings; it explains the two ways out and gives the exact command or
 * setting to copy. Tested under node (tests/test_frontend_coverage.py).
 */
(function (root) {
  "use strict";

  const CODE = "routing_area_not_covered";
  const SEGMENT_RE = /^[EW]\d{1,3}_[NS]\d{1,2}$/;
  const DEFAULT_BASE = "https://brouter.de/brouter/segments4/";

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** The coverage error among a response's errors, or null. */
  function find(errors) {
    return (errors || []).find((e) => e && e.code === CODE) || null;
  }

  /** What is needed, read defensively: only well-formed tile names and an https source survive. */
  function details(error) {
    const d = (error && error.detail) || {};
    const valid = (list) => (Array.isArray(list) ? list.filter((s) => SEGMENT_RE.test(String(s))) : []);
    const reported = valid(d.reported_missing || d.segments);
    const touched = valid(d.segments);
    const base = typeof d.download_base === "string" && /^https:\/\/[^\s]+\/$/.test(d.download_base) ? d.download_base : DEFAULT_BASE;
    const engines = Array.isArray(d.configured_engines) ? d.configured_engines.map(String) : [];
    return {
      missing: reported,
      others: touched.filter((s) => !reported.includes(s)),
      base,
      engines,
    };
  }

  /** The shell commands that fetch the missing tiles into BRouter's segment folder. */
  function downloadCommand(segments, base) {
    const lines = segments.map((s) => `curl -fL --create-dirs -o segments/${s}.rd5 ${base}${s}.rd5`);
    return ["cd docker/brouter", ...lines].join("\n");
  }

  const ENV_LINE = "ROUTING_PROVIDER=all";

  /**
   * The card that asks the user what to do. `hasRoute` is true when another engine already
   * answered, so the page below is a result and not a failure.
   */
  function cardHtml(error, hasRoute) {
    const d = details(error);
    if (!d.missing.length && !d.others.length) return "";
    const names = (d.missing.length ? d.missing : d.others).map((s) => `<code>${escapeHtml(s)}</code>`).join(", ");
    const lead = hasRoute
      ? `BRouter has no map data for this part of the trip (tile ${names}), so the result below comes from the other engine.`
      : `BRouter has no map data for this part of the trip (tile ${names}), so no route could be made.`;
    const parts = [`<h3>Map data missing</h3><p>${lead} Nothing is downloaded or changed automatically &mdash; what would you like?</p>`];

    if (d.missing.length) {
      parts.push(
        '<div class="coverage-option"><strong>1. Download the missing tile' +
          (d.missing.length > 1 ? "s" : "") +
          "</strong> &mdash; each 5&deg;&times;5&deg; tile is large (typically 100&ndash;200&nbsp;MB), from the official " +
          '<a href="' + escapeHtml(d.base) + '" target="_blank" rel="noopener noreferrer">brouter.de</a>.' +
          `<pre id="coverage-download">${escapeHtml(downloadCommand(d.missing, d.base))}</pre>` +
          '<button type="button" class="ghost" data-copy="coverage-download">Copy</button>' +
          '<p class="hint">Run it in the project folder on the machine that hosts BRouter, restart the BRouter container, then plan again.' +
          (d.others.length
            ? ` The trip also passes through ${d.others.map((s) => `<code>${escapeHtml(s)}</code>`).join(", ")}; you only need those if you do not have them yet.`
            : "") +
          "</p></div>",
      );
    }
    if (d.engines.length < 2) {
      parts.push(
        '<div class="coverage-option"><strong>' + (d.missing.length ? "2" : "1") + ". Use all routing engines</strong> &mdash; openrouteservice then answers where BRouter has no data." +
          `<pre id="coverage-env">${escapeHtml(ENV_LINE)}</pre>` +
          '<button type="button" class="ghost" data-copy="coverage-env">Copy</button>' +
          '<p class="hint">Put it in <code>.env</code> and restart the app. Its routes can outrank the BRouter gravel profile, and an engine that is not running (Valhalla, say) shows up as an error and a degraded status, while the others still answer.</p></div>',
      );
    } else {
      parts.push(
        `<p class="hint">Several engines are already configured (${d.engines.map(escapeHtml).join(", ")}).</p>`,
      );
    }
    return parts.join("");
  }

  const api = { CODE, find, details, downloadCommand, cardHtml, ENV_LINE, escapeHtml };
  root.BikeCoverage = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
