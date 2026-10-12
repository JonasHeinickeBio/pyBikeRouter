/* Route optimiser helpers (pure functions, no DOM access).
 *
 * The "Optimize" tab builds the request's `optimize` spec and shows the report the server
 * returns (docs/optimizer.md). Everything shown comes from that report; nothing is computed
 * here beyond formatting, and every string from the server is escaped.
 */
(function (root) {
  "use strict";

  /** Goals the tab offers, in display order. `needs` names a server capability. */
  const GOALS = [
    { metric: "traffic", label: "Less traffic", hint: "quiet roads, estimated from the road tags" },
    { metric: "paved", label: "More asphalt", hint: "as much paved surface as possible", opposite: "unpaved" },
    { metric: "pois", label: "More sights along the track", hint: "well-known places near the route", needs: "pois" },
    { metric: "busy_roads", label: "Fewer busy roads", hint: "the smallest share on busy roads" },
    { metric: "unpaved", label: "More off-road", hint: "gravel and trails", opposite: "paved" },
    { metric: "cobbles", label: "Fewer cobbles", hint: "smoother than cobblestones" },
    { metric: "ascent", label: "Less climbing", hint: "the flattest route" },
    { metric: "duration", label: "Faster", hint: "the quickest route" },
    { metric: "distance", label: "Shorter", hint: "the shortest route" },
  ];

  const LEVELS = [
    { level: "high", label: "Most important", weight: 1.0 },
    { level: "medium", label: "Important", weight: 0.7 },
    { level: "low", label: "Nice to have", weight: 0.4 },
  ];

  /** Starting points: each is a list of {metric, level}. */
  const PRESETS = [
    {
      id: "quiet-smooth",
      label: "Quiet and smooth",
      goals: [{ metric: "traffic", level: "high" }, { metric: "paved", level: "medium" }],
    },
    {
      id: "sights",
      label: "Sights, little traffic",
      goals: [{ metric: "pois", level: "high" }, { metric: "traffic", level: "medium" }],
    },
    {
      id: "fast-flat",
      label: "Fast and flat",
      goals: [{ metric: "duration", level: "high" }, { metric: "ascent", level: "medium" }],
    },
  ];

  const MAX_GOALS = 4; // the API's limit

  function weightOf(level) {
    const found = LEVELS.find((l) => l.level === level);
    return found ? found.weight : 0.7;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /**
   * The `optimize` spec for the request, or `{ error }` when the choice cannot be sent.
   * `choice`: { goals: [{metric, level}], maxDetourPct, effort, poi: {corridorM, minFame} }.
   */
  function buildSpec(choice) {
    const goals = (choice.goals || []).filter((g) => GOALS.some((d) => d.metric === g.metric));
    if (!goals.length) return { error: "Pick at least one goal." };
    if (goals.length > MAX_GOALS) return { error: `Pick at most ${MAX_GOALS} goals.` };
    const metrics = goals.map((g) => g.metric);
    if (new Set(metrics).size !== metrics.length) return { error: "Each goal can be picked once." };
    if (metrics.includes("paved") && metrics.includes("unpaved")) {
      return { error: "More asphalt and more off-road contradict each other." };
    }
    const spec = {
      goals: goals.map((g) => ({ metric: g.metric, weight: weightOf(g.level) })),
      effort: ["quick", "normal", "thorough"].includes(choice.effort) ? choice.effort : "normal",
    };
    const detour = Number(choice.maxDetourPct);
    if (Number.isFinite(detour) && detour >= 0 && detour <= 300) spec.max_detour_pct = detour;
    if (metrics.includes("pois") && choice.poi) {
      const poi = {};
      const corridor = Number(choice.poi.corridorM);
      const fame = Number(choice.poi.minFame);
      if (Number.isFinite(corridor) && corridor >= 50 && corridor <= 2000) poi.corridor_m = corridor;
      if (Number.isFinite(fame) && fame >= 1 && fame <= 200) poi.min_fame = Math.trunc(fame);
      if (Object.keys(poi).length) spec.poi = poi;
    }
    return { spec };
  }

  /** How long it may take, in plain words, for the progress message. */
  function waitText(effort) {
    return effort === "quick"
      ? "about half a minute"
      : effort === "thorough"
        ? "several minutes for a long trip"
        : "up to a minute or two";
  }

  /** The report's goals as a sentence: "less traffic and more asphalt". */
  function goalPhrase(report) {
    const labels = (report.goals || []).map((g) => String(g.label || g.metric));
    if (labels.length <= 1) return labels.join("");
    return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
  }

  /** Column headings: the goal's name in a word or two. */
  const SHORT = {
    traffic: "Traffic",
    busy_roads: "Busy roads",
    paved: "Paved",
    unpaved: "Unpaved",
    cobbles: "Cobbles",
    pois: "Sights",
    ascent: "Climb",
    duration: "Time",
    distance: "Length",
  };

  /** A goal's value in as few characters as the table can use ("0.12", "97 %", "9", "2.7 h"). */
  function compact(metric, value) {
    if (value === undefined || value === null) return null;
    const v = Number(value);
    if (!Number.isFinite(v)) return null;
    switch (metric) {
      case "traffic":
        return v.toFixed(2);
      case "busy_roads":
      case "paved":
      case "unpaved":
      case "cobbles":
        return `${Math.round(v * 100)}\u00a0%`;
      case "pois":
        return String(Math.round(v));
      case "distance":
        return `${(v / 1000).toFixed(1)}\u00a0km`;
      case "duration":
        return `${(v / 3600).toFixed(1)}\u00a0h`;
      case "ascent":
        return `${Math.round(v)}\u00a0m`;
      default:
        return String(v);
    }
  }

  function cell(row, metric) {
    const short = compact(metric, (row.values || {})[metric]);
    if (short === null) return "&mdash;";
    const full = (row.shown || {})[metric];
    return `<span title="${escapeHtml(full || short)}">${escapeHtml(short)}</span>`;
  }

  /** "brouter/trekking" -> "trekking" when every route comes from the same engine. */
  function labeller(rows) {
    const providers = new Set(rows.map((r) => String(r.label || "").split("/")[0]));
    return providers.size === 1
      ? (label) => String(label).replace(/^[^/]*\//, "")
      : (label) => String(label);
  }

  /** The table of compared routes, best fit first; rows carry `data-index` for the map. */
  function tableHtml(report) {
    const rows = report.table || [];
    const goals = report.goals || [];
    if (!rows.length || !goals.length) return "";
    const head =
      "<tr><th>Route</th><th class='num'>Length</th><th class='num'>Detour</th>" +
      goals
        .map(
          (g) =>
            `<th class='num' title='${escapeHtml(g.label || g.metric)}'>` +
            `${escapeHtml(SHORT[g.metric] || g.metric)}</th>`,
        )
        .join("") +
      "<th class='num' title='weighted fit to the goals, 0 to 1'>Fit</th></tr>";
    const name = labeller(rows);
    const body = rows
      .map((r, i) => {
        const flags = [];
        if (r.selected) flags.push("<span class='badge'>best fit</span>");
        if (!r.feasible) flags.push("<span class='badge warn' title='over the detour cap or a hard limit'>over limit</span>");
        return (
          `<tr class="cmp-row${r.selected ? " selected" : ""}" data-index="${i}" tabindex="0">` +
          `<td class="opt-name"><div class="opt-label">${escapeHtml(name(r.label || ""))}</div>` +
          `${flags.join(" ")}</td>` +
          `<td class="num">${Number(r.distance_km).toFixed(1)} km</td>` +
          `<td class="num">+${Number(r.detour_pct || 0).toFixed(0)}&nbsp;%</td>` +
          goals.map((g) => `<td class="num">${cell(r, g.metric)}</td>`).join("") +
          `<td class="num">${Number(r.utility).toFixed(2)}</td></tr>`
        );
      })
      .join("");
    return `<div class="candidates-scroll"><table class="opt-table">${head}${body}</table></div>`;
  }

  /** The whole report as markup for the panel: summary, places passed, trade-offs, table. */
  function reportHtml(report) {
    if (!report || !report.summary) return "";
    const parts = [`<p class="opt-summary">${escapeHtml(report.summary)}</p>`];
    const dropped = report.dropped_goals || [];
    if (dropped.length) {
      parts.push(
        "<p class='hint'>Not applied: " +
          dropped.map((d) => `${escapeHtml(d.metric)} (${escapeHtml(d.reason)})`).join("; ") +
          ".</p>",
      );
    }
    if (report.pois && report.pois.length) {
      parts.push(
        `<p class="opt-pois"><strong>It passes:</strong> ${report.pois.map(escapeHtml).join(", ")}.</p>`,
      );
    } else if (report.poi_status === "unavailable") {
      parts.push("<p class='hint'>The sight lookup was not available, so places were not counted.</p>");
    }
    if (report.tradeoffs && report.tradeoffs.length) {
      parts.push(
        "<ul class='opt-tradeoffs'>" +
          report.tradeoffs.map((t) => `<li>${escapeHtml(t)}</li>`).join("") +
          "</ul>",
      );
    }
    parts.push(tableHtml(report));
    parts.push(
      "<p class='hint'>Click a row to see that route on the map. The fit is a weighted mean of how " +
        "close each route is to the best one for every goal; the winner is the highest fit within the " +
        "detour cap. Traffic is an estimate from OpenStreetMap tags, not a count of cars.</p>",
    );
    return parts.join("");
  }

  const api = {
    GOALS, LEVELS, PRESETS, MAX_GOALS, weightOf, buildSpec, waitText, goalPhrase, compact,
    tableHtml, reportHtml, escapeHtml,
  };
  root.BikeOptimize = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
