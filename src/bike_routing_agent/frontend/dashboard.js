/* Evaluation dashboard (issue #7): renders GET /v1/history/stats. */
"use strict";

const $ = (id) => document.getElementById(id);

const els = {
  form: $("filters"),
  bikeType: $("f-bike-type"),
  since: $("f-since"),
  until: $("f-until"),
  status: $("dash-status"),
  body: $("dash-body"),
  kpis: $("kpis"),
  engines: $("engines"),
  breakdown: $("breakdown"),
  daily: $("daily"),
};

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const pct = (x) => (x == null ? "—" : `${(x * 100).toFixed(0)}%`);
const num = (x, digits = 2) => (x == null ? "—" : Number(x).toFixed(digits));
const km = (m) => (m == null ? "—" : `${(m / 1000).toFixed(1)} km`);
const mins = (s) => (s == null ? "—" : `${Math.round(s / 60)} min`);
const meters = (m) => (m == null ? "—" : `${Math.round(m)} m`);

function setStatus(kind, message) {
  els.status.hidden = !message;
  els.status.className = `status ${kind || ""}`;
  els.status.textContent = message || "";
}

/* The date inputs are whole UTC days; `until` is inclusive in the UI and
   becomes the next day's midnight for the API's exclusive bound. */
function buildQuery() {
  const params = new URLSearchParams();
  if (els.bikeType.value) params.set("bike_type", els.bikeType.value);
  if (els.since.value) params.set("since", `${els.since.value}T00:00:00Z`);
  if (els.until.value) {
    const end = new Date(`${els.until.value}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 1);
    params.set("until", end.toISOString());
  }
  return params.toString();
}

async function load() {
  setStatus("loading", "Loading…");
  els.body.hidden = true;
  const query = buildQuery();
  let resp;
  try {
    resp = await fetch(`/v1/history/stats${query ? `?${query}` : ""}`);
  } catch (err) {
    setStatus("error", `Network error: ${err}`);
    return;
  }
  if (resp.status === 503) {
    setStatus(
      "error",
      "Route history is not configured on this server (set DATABASE_URL to record plans).",
    );
    return;
  }
  if (!resp.ok) {
    setStatus("error", `Request failed (HTTP ${resp.status}).`);
    return;
  }
  render(await resp.json());
}

function render(stats) {
  if (stats.total_plans === 0) {
    setStatus("info", "No recorded plans match these filters.");
    return;
  }
  setStatus("", "");
  els.body.hidden = false;
  renderKpis(stats);
  renderEngines(stats.providers);
  renderBreakdown(stats.providers);
  renderDaily(stats.daily);
}

function renderKpis(stats) {
  const failed = stats.total_plans - (stats.by_status.ready || 0);
  const statuses = Object.entries(stats.by_status)
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => `${escapeHtml(name)}: ${n}`)
    .join(" · ");
  const kpi = (value, label) =>
    `<div class="kpi"><span class="value">${value}</span><span class="label">${label}</span></div>`;
  els.kpis.className = "panel kpis";
  els.kpis.innerHTML =
    kpi(stats.total_plans, "plans") +
    kpi(pct(stats.ready_rate), "ready") +
    kpi(failed, "not ready") +
    `<div class="kpi"><span class="label">${statuses}</span></div>`;
}

function renderEngines(providers) {
  if (!providers.length) {
    els.engines.innerHTML = "<tr><td>No ready plans with candidates yet.</td></tr>";
    return;
  }
  const head =
    "<tr><th>Engine</th><th>Profile</th><th class='num'>Plans</th><th>Win rate</th>" +
    "<th class='num'>Score</th><th class='num'>Distance</th><th class='num'>Time</th>" +
    "<th class='num'>Ascent</th></tr>";
  const rows = providers
    .map(
      (p) =>
        `<tr><td>${escapeHtml(p.provider)}</td><td>${escapeHtml(p.provider_profile)}</td>` +
        `<td class="num">${p.candidates}</td>` +
        `<td><span class="winbar"><span style="width:${(p.win_rate * 100).toFixed(0)}%"></span></span>` +
        `${pct(p.win_rate)} <small>(${p.selected}/${p.candidates})</small></td>` +
        `<td class="num">${num(p.mean_score)}</td><td class="num">${km(p.mean_distance_m)}</td>` +
        `<td class="num">${mins(p.mean_duration_s)}</td><td class="num">${meters(p.mean_ascent_m)}</td></tr>`,
    )
    .join("");
  els.engines.innerHTML = head + rows;
}

function renderBreakdown(providers) {
  const components = [...new Set(providers.flatMap((p) => Object.keys(p.mean_score_breakdown)))].sort();
  if (!components.length) {
    els.breakdown.innerHTML = "<tr><td>No score breakdowns recorded.</td></tr>";
    return;
  }
  const head =
    "<tr><th>Engine / profile</th>" +
    components.map((c) => `<th class="num">${escapeHtml(c.replace(/_/g, " "))}</th>`).join("") +
    "</tr>";
  const rows = providers
    .map(
      (p) =>
        `<tr><td>${escapeHtml(p.provider)} / ${escapeHtml(p.provider_profile)}</td>` +
        components.map((c) => `<td class="num">${num(p.mean_score_breakdown[c])}</td>`).join("") +
        "</tr>",
    )
    .join("");
  els.breakdown.innerHTML = head + rows;
}

function renderDaily(daily) {
  const max = Math.max(...daily.map((d) => d.total), 1);
  els.daily.innerHTML = daily
    .map((d) => {
      const ready = d.by_status.ready || 0;
      const other = d.total - ready;
      const h = (n) => `${((n / max) * 100).toFixed(1)}%`;
      const detail = Object.entries(d.by_status)
        .map(([name, n]) => `${name}: ${n}`)
        .join(", ");
      return (
        `<div class="day" tabindex="0" title="${escapeHtml(d.date)} — ${escapeHtml(detail)}">` +
        `<div class="stack" style="height:${h(d.total)}">` +
        `<div class="seg-ready" style="height:${ready ? (ready / d.total) * 100 : 0}%"></div>` +
        `<div class="seg-other" style="height:${other ? (other / d.total) * 100 : 0}%"></div>` +
        `</div><div class="stamp">${escapeHtml(d.date.slice(5))}</div></div>`
      );
    })
    .join("");
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  load();
});

load();
