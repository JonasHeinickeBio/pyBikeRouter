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
    // Into a .part file first: a transfer that dies half way must not leave a truncated tile
    // under the name BRouter reads (it would be taken for a real, broken one).
    const lines = segments.map(
      (s) => `curl -fL --create-dirs -o segments/${s}.rd5.part ${base}${s}.rd5 && mv segments/${s}.rd5.part segments/${s}.rd5`,
    );
    return ["cd docker/brouter", ...lines].join("\n");
  }

  const ENV_LINE = "ROUTING_PROVIDER=all";

  function megabytes(bytes) {
    return typeof bytes === "number" && bytes > 0 ? `${Math.round(bytes / 1e6)} MB` : null;
  }

  function copyBlock(id, text) {
    return (
      `<pre id="${id}">${escapeHtml(text)}</pre>` +
      `<button type="button" class="ghost" data-copy="${id}">Copy</button>`
    );
  }

  /**
   * The card that asks the user what to do. `hasRoute` is true when another engine already
   * answered, so the page below is a result and not a failure. `ctx` is what the server
   * offers: `downloads` (it can fetch tiles itself), `engines` (names a request may use),
   * `info` ({name: {present, size_bytes}} from /v1/routing/segments).
   */
  function cardHtml(error, hasRoute, ctx) {
    const c = ctx || {};
    const d = details(error);
    if (!d.missing.length && !d.others.length) return "";
    const names = (d.missing.length ? d.missing : d.others).map((s) => `<code>${escapeHtml(s)}</code>`).join(", ");
    const lead = hasRoute
      ? `BRouter has no map data for this part of the trip (tile ${names}), so the result below comes from the other engine.`
      : `BRouter has no map data for this part of the trip (tile ${names}), so no route could be made.`;
    const parts = [`<h3>Map data missing</h3><p>${lead} Nothing is downloaded or changed until you choose &mdash; what would you like?</p>`];
    let n = 0;

    if (d.missing.length) {
      n += 1;
      const info = c.info || {};
      const sizes = d.missing.map((s) => (info[s] && info[s].size_bytes) || null);
      const total = sizes.every((x) => x) ? sizes.reduce((a, b) => a + b, 0) : null;
      const sizeText = megabytes(total);
      const command = copyBlock("coverage-download", downloadCommand(d.missing, d.base));
      let body = `<strong>${n}. Download the missing tile${d.missing.length > 1 ? "s" : ""}</strong> &mdash; each 5&deg;&times;5&deg; tile is large (typically 100&ndash;200&nbsp;MB), from the official <a href="${escapeHtml(d.base)}" target="_blank" rel="noopener noreferrer">brouter.de</a>.`;
      if (c.downloads) {
        const already = d.missing.filter((s) => info[s] && info[s].present);
        body +=
          `<div class="coverage-actions"><button type="button" class="primary" data-action="download" data-segments="${escapeHtml(d.missing.join(","))}">` +
          `Download ${d.missing.map(escapeHtml).join(", ")}${sizeText ? ` (${escapeHtml(sizeText)})` : ""} now</button></div>` +
          '<div id="coverage-progress" class="coverage-progress" aria-live="polite"></div>' +
          '<p class="hint">The server fetches it and plans the trip again when it is done. ' +
          (already.length
            ? `${already.map((s) => `<code>${escapeHtml(s)}</code>`).join(", ")} is already on disk &mdash; BRouter probably needs a restart to see it. `
            : "") +
          "</p>" +
          `<details><summary>Or run it yourself</summary>${command}</details>`;
      } else {
        body += command + '<p class="hint">Run it in the project folder on the machine that hosts BRouter, restart the BRouter container, then plan again. (The server can do this for you when <code>BROUTER_SEGMENTS_DIR</code> is set.)</p>';
      }
      if (d.others.length) {
        body += `<p class="hint">The trip also passes through ${d.others.map((s) => `<code>${escapeHtml(s)}</code>`).join(", ")}; you only need those if you do not have them yet.</p>`;
      }
      parts.push(`<div class="coverage-option">${body}</div>`);
    }

    if (d.engines.length < 2) {
      n += 1;
      const offered = Array.isArray(c.engines) ? c.engines : [];
      const canOrs = offered.includes("ors") && !d.engines.includes("ors");
      let body = `<strong>${n}. Use openrouteservice instead</strong> &mdash; it answers where BRouter has no data, but its route for your bike type can differ from the BRouter profile (gravel &rarr; &ldquo;regular&rdquo;).`;
      if (canOrs) {
        body +=
          '<div class="coverage-actions"><button type="button" class="primary" data-action="use-engine" data-engine="ors">Plan this trip with openrouteservice</button></div>' +
          '<p class="hint">This trip only. To make it permanent (ORS next to BRouter, best-scoring route wins), put the line below in <code>.env</code> and restart the app.</p>';
      } else {
        body += '<p class="hint">Put the line below in <code>.env</code> and restart the app.</p>';
      }
      body += `<details${canOrs ? "" : " open"}><summary>The setting</summary>${copyBlock("coverage-env", ENV_LINE)}<p class="hint">With <code>all</code> an engine that is not running (Valhalla, say) shows up as an error and a degraded status, while the others still answer.</p></details>`;
      parts.push(`<div class="coverage-option">${body}</div>`);
    } else {
      parts.push(`<p class="hint">Several engines are already configured (${d.engines.map(escapeHtml).join(", ")}).</p>`);
    }
    return parts.join("");
  }

  /** The progress of a tile download job (from GET /v1/routing/segments/download/{id}). */
  function progressHtml(job) {
    if (!job) return "";
    const rows = (job.segments || []).map((s) => {
      const total = megabytes(s.total_bytes);
      const done = s.bytes ? Math.round(s.bytes / 1e6) : 0;
      const pct = s.total_bytes ? Math.min(100, Math.round((100 * s.bytes) / s.total_bytes)) : null;
      let text;
      switch (s.state) {
        case "present": text = "already on disk"; break;
        case "pending": text = "waiting"; break;
        case "downloading": text = total ? `${done} of ${total}${pct !== null ? ` (${pct}%)` : ""}` : "starting"; break;
        case "done": text = total ? `done (${total})` : "done"; break;
        default: text = `failed${s.error ? `: ${s.error}` : ""}`;
      }
      return `<div class="coverage-row ${escapeHtml(s.state)}"><code>${escapeHtml(s.name)}</code> ${escapeHtml(text)}</div>`;
    });
    if (job.state === "failed" && job.error && !rows.some((r) => r.includes(escapeHtml(job.error)))) {
      rows.push(`<div class="coverage-row failed">${escapeHtml(job.error)}</div>`);
    }
    return rows.join("");
  }

  const api = { CODE, find, details, downloadCommand, cardHtml, progressHtml, megabytes, ENV_LINE, escapeHtml };
  root.BikeCoverage = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
