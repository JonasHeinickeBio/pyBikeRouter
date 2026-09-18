/* pyBikeRouter frontend: form -> POST /v1/route/plan -> Leaflet map + results. */
"use strict";

const API_BASE = "";  // same origin

const $ = (id) => document.getElementById(id);

const els = {
  origin: $("origin-input"),
  destination: $("destination-input"),
  viaList: $("via-list"),
  addVia: $("add-via"),
  swap: $("swap"),
  bikeType: $("bike-type"),
  targetDistance: $("target-distance"),
  maxDistance: $("max-distance"),
  maxAscent: $("max-ascent"),
  preferSurfaces: $("prefer-surfaces"),
  avoidSurfaces: $("avoid-surfaces"),
  avoidTraffic: $("avoid-traffic"),
  avoidFerries: $("avoid-ferries"),
  returnOrigin: $("return-origin"),
  planBtn: $("plan-btn"),
  resetBtn: $("reset-btn"),
  status: $("status"),
  clarificationPanel: $("clarification-panel"),
  clarificationList: $("clarification-list"),
  resultsPanel: $("results-panel"),
  explanation: $("explanation"),
  metrics: $("metrics"),
  breakdownDetails: $("breakdown-details"),
  scoreBreakdown: $("score-breakdown"),
  surfacesDetails: $("surfaces-details"),
  surfaceTable: $("surface-table"),
  warnings: $("warnings"),
  artifacts: $("artifacts"),
  provenance: $("provenance"),
  errorsPanel: $("errors-panel"),
  errorList: $("error-list"),
  mapModeBanner: $("map-mode-banner"),
  mapModeKind: $("map-mode-kind"),
  mapModeCancel: $("map-mode-cancel"),
  map: $("map"),
};

const COORD_RE = /^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/;

const state = {
  map: null,
  routeLayer: null,
  placeMarkers: L.layerGroup(),
  pickingTarget: null, // "origin" | "destination" | { viaRow: element }
  lastResponse: null,
  aborted: null,
};

/* ------------------------------- map setup ------------------------------- */

function initMap() {
  state.map = L.map(els.map, { zoomControl: true }).setView([51.75, -1.25], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(state.map);
  state.placeMarkers.addTo(state.map);
  state.map.on("click", onMapClick);
}

function onMapClick(ev) {
  if (!state.pickingTarget) return;
  const lat = ev.latlng.lat.toFixed(5);
  const lon = ev.latlng.lng.toFixed(5);
  const value = `${lat}, ${lon}`;
  if (state.pickingTarget === "origin") {
    els.origin.value = value;
    markCoordInput(els.origin);
  } else if (state.pickingTarget === "destination") {
    els.destination.value = value;
    markCoordInput(els.destination);
  } else if (state.pickingTarget.viaInput) {
    state.pickingTarget.viaInput.value = value;
    markCoordInput(state.pickingTarget.viaInput);
  }
  stopPicking();
  redrawPlaceMarkers();
}

function startPicking(target, kindLabel, btn) {
  state.pickingTarget = target;
  els.map.classList.add("picking");
  els.mapModeKind.textContent = kindLabel;
  els.mapModeBanner.hidden = false;
  if (btn) btn.classList.add("arming");
}

function stopPicking() {
  state.pickingTarget = null;
  els.map.classList.remove("picking");
  els.mapModeBanner.hidden = true;
  document.querySelectorAll(".icon-btn.arming").forEach((b) => b.classList.remove("arming"));
}

/* ------------------------------ place inputs ----------------------------- */

function markCoordInput(input) {
  input.classList.toggle("set-by-coord", COORD_RE.test(input.value));
}

function addViaRow(value = "") {
  const row = document.createElement("div");
  row.className = "via-row";

  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Via place or lat, lon";
  input.value = value;
  input.addEventListener("input", () => markCoordInput(input));

  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "icon-btn";
  pick.title = "Pick on map";
  pick.innerHTML = "&#9673;";
  pick.addEventListener("click", () => {
    const active = state.pickingTarget && state.pickingTarget.viaInput === input;
    stopPicking();
    if (!active) startPicking({ viaInput: input }, "via point", pick);
  });

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "via-remove";
  remove.title = "Remove via point";
  remove.innerHTML = "&times;";
  remove.addEventListener("click", () => {
    row.remove();
    redrawPlaceMarkers();
  });

  row.append(input, pick, remove);
  els.viaList.appendChild(row);
}

function viaInputs() {
  return [...els.viaList.querySelectorAll("input")];
}

function swapPlaces() {
  const a = els.origin.value;
  els.origin.value = els.destination.value;
  els.destination.value = a;
  markCoordInput(els.origin);
  markCoordInput(els.destination);
  redrawPlaceMarkers();
}

/* ------------------------------ request build ---------------------------- */

function placeValue(text) {
  const trimmed = text.trim();
  const m = COORD_RE.exec(trimmed);
  if (m) {
    const lat = parseFloat(m[1]);
    const lon = parseFloat(m[2]);
    if (lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
      return { lat, lon };
    }
  }
  return trimmed;
}

function csvList(text) {
  return text.split(",").map((s) => s.trim()).filter(Boolean);
}

function buildConstraints() {
  const num = (el) => (el.value.trim() === "" ? null : Number(el.value));
  const constraints = {
    bike_type: els.bikeType.value,
    target_distance_km: num(els.targetDistance),
    max_distance_km: num(els.maxDistance),
    max_ascent_m: num(els.maxAscent),
    prefer_surfaces: csvList(els.preferSurfaces.value),
    avoid_surfaces: csvList(els.avoidSurfaces.value),
    avoid_high_traffic_roads: els.avoidTraffic.checked,
    avoid_ferries: els.avoidFerries.checked,
    return_to_origin: els.returnOrigin.checked,
  };
  return constraints;
}

function buildRequest() {
  return {
    origin: placeValue(els.origin.value),
    destination: placeValue(els.destination.value),
    via: viaInputs().map((i) => placeValue(i.value)).filter((v) => v !== ""),
    constraints: buildConstraints(),
  };
}

/* -------------------------------- requests ------------------------------- */

function setStatus(kind, html) {
  if (!html) {
    els.status.hidden = true;
    return;
  }
  els.status.hidden = false;
  els.status.className = `status ${kind}`;
  els.status.innerHTML = html;
}

async function planRoute() {
  stopPicking();
  const payload = buildRequest();
  if (typeof payload.origin !== "object" && payload.origin === "") {
    setStatus("error", "Please enter an origin.");
    return;
  }
  if (typeof payload.destination !== "object" && payload.destination === "") {
    setStatus("error", "Please enter a destination.");
    return;
  }

  els.planBtn.disabled = true;
  setStatus("loading", "Planning route&hellip; <em>geocoding and routing can take a few seconds</em>");
  clearResults();

  if (state.aborted) state.aborted.abort();
  const controller = new AbortController();
  state.aborted = controller;
  const startedAt = performance.now();

  try {
    const resp = await fetch(`${API_BASE}/v1/route/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const detail = await safeErrorText(resp);
      setStatus("error", `Request failed (HTTP ${resp.status}): ${escapeHtml(detail)}`);
      return;
    }
    const data = await resp.json();
    state.lastResponse = data;
    const secs = ((performance.now() - startedAt) / 1000).toFixed(1);
    renderResponse(data, secs);
  } catch (err) {
    if (err.name === "AbortError") return;
    setStatus("error", `Network error: ${escapeHtml(String(err))}`);
  } finally {
    els.planBtn.disabled = false;
  }
}

async function safeErrorText(resp) {
  try {
    const body = await resp.json();
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ");
    }
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body);
  } catch {
    return await resp.text().catch(() => resp.statusText);
  }
}

/* ------------------------------ render results --------------------------- */

function clearResults() {
  if (state.routeLayer) {
    state.map.removeLayer(state.routeLayer);
    state.routeLayer = null;
  }
  els.resultsPanel.hidden = true;
  els.errorsPanel.hidden = true;
  els.clarificationPanel.hidden = true;
  els.clarificationList.innerHTML = "";
}

function renderResponse(data, secs) {
  clearResults();
  renderErrors(data.errors);

  switch (data.status) {
    case "ready":
      setStatus("ok", `Route ready in ${secs}s.`);
      renderRoute(data);
      break;
    case "awaiting_clarification":
      setStatus("info", "Your places are ambiguous &mdash; pick the intended one below.");
      renderClarification(data);
      break;
    case "invalid":
      setStatus("error", "The request was invalid.");
      break;
    case "no_route":
      setStatus("info", "No route found for these places and constraints.");
      break;
    default:
      setStatus("error", `The routing provider failed (status: ${escapeHtml(data.status)}).`);
  }
}

function renderErrors(errors) {
  if (!errors || errors.length === 0) {
    els.errorsPanel.hidden = true;
    return;
  }
  els.errorsPanel.hidden = false;
  els.errorList.innerHTML = errors
    .map((e) => `<li><code>${escapeHtml(e.code || "error")}</code>: ${escapeHtml(e.message || JSON.stringify(e))}</li>`)
    .join("");
}

function renderClarification(data) {
  els.clarificationPanel.hidden = false;
  els.clarificationList.innerHTML = "";
  for (const group of data.clarification) {
    const wrap = document.createElement("div");
    wrap.className = "clarify-group";
    const title = document.createElement("h3");
    title.textContent = `“${group.field}”`;
    wrap.appendChild(title);

    if (!group.candidates.length) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "No matches found — try a different description.";
      wrap.appendChild(p);
    }
    for (const cand of group.candidates) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "clarify-option";
      btn.innerHTML = `${escapeHtml(cand.label)}<span class="conf">${Math.round(cand.confidence * 100)}% · ${escapeHtml(cand.source)}</span>`;
      btn.addEventListener("click", () => resolveClarification(group.field, cand));
      wrap.appendChild(btn);
    }
    els.clarificationList.appendChild(wrap);
  }
}

function resolveClarification(field, candidate) {
  const coordText = `${candidate.coordinate.lat.toFixed(5)}, ${candidate.coordinate.lon.toFixed(5)}`;
  const inputs = [els.origin, els.destination, ...viaInputs()];
  const match = inputs.find(
    (i) => i.value.trim() === field && !COORD_RE.test(i.value),
  );
  if (match) {
    match.value = coordText;
    markCoordInput(match);
  } else {
    setStatus("info", `Could not map “${escapeHtml(field)}” back to an input — enter the coordinate manually: ${escapeHtml(coordText)}`);
    return;
  }
  redrawPlaceMarkers();
  planRoute();
}

function renderRoute(data) {
  const route = data.route;
  if (!route) return;

  state.routeLayer = L.geoJSON(route.geometry_geojson, {
    style: { color: "#0e7a4a", weight: 5, opacity: 0.85 },
  }).addTo(state.map);
  state.map.fitBounds(state.routeLayer.getBounds().pad(0.12));

  redrawPlaceMarkers();

  els.resultsPanel.hidden = false;
  els.explanation.textContent = data.explanation || "";

  const m = route.metrics || {};
  const metric = (value, label) =>
    `<div class="metric"><div class="value">${value}</div><div class="label">${label}</div></div>`;
  els.metrics.innerHTML =
    metric(fmtKm(m.distance_m), "Distance") +
    metric(fmtDuration(m.duration_s), "Time") +
    metric(fmtM(m.ascent_m), "Ascent") +
    metric(fmtM(m.descent_m), "Descent") +
    metric(route.score != null ? route.score.toFixed(2) : "—", "Score") +
    metric(escapeHtml(route.provider_profile || "—"), "Profile");

  renderScoreBreakdown(route.score_breakdown);
  renderSurfaces(m.surface_coverage, m.unknown_surface_fraction);

  els.warnings.innerHTML = (route.warnings || [])
    .map((w) => `<li>${escapeHtml(w)}</li>`)
    .join("");

  renderArtifacts(data.artifacts);

  const provBits = [];
  if (route.provider) provBits.push(`provider: ${route.provider}`);
  const prov = route.provenance || {};
  for (const key of ["requested_at", "profile", "profile_mapping"]) {
    if (prov[key] !== undefined) provBits.push(`${key}: ${JSON.stringify(prov[key])}`);
  }
  els.provenance.textContent = provBits.join(" · ");
}

function renderScoreBreakdown(breakdown) {
  const entries = Object.entries(breakdown || {});
  if (!entries.length) {
    els.breakdownDetails.hidden = true;
    return;
  }
  els.breakdownDetails.hidden = false;
  els.scoreBreakdown.innerHTML =
    "<tr><th>Component</th><th class='num'>Value</th></tr>" +
    entries
      .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td class="num">${Number(v).toFixed(3)}</td></tr>`)
      .join("");
}

function renderSurfaces(coverage, unknownFraction) {
  const entries = Object.entries(coverage || {});
  if (!entries.length && unknownFraction == null) {
    els.surfacesDetails.hidden = true;
    return;
  }
  els.surfacesDetails.hidden = false;
  const pct = (x) => `${(x * 100).toFixed(1)}%`;
  els.surfaceTable.innerHTML =
    "<tr><th>Surface</th><th class='num'>Share</th></tr>" +
    entries
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td class="num">${pct(v)}</td></tr>`)
      .join("") +
    (unknownFraction != null
      ? `<tr><td><em>unknown</em></td><td class="num">${pct(unknownFraction)}</td></tr>`
      : "");
}

function renderArtifacts(artifacts) {
  els.artifacts.innerHTML = "";
  const entries = Object.entries(artifacts || {});
  if (!entries.length) return;
  for (const [key, url] of entries) {
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    a.textContent = `&#11015; ${key.replace(/_url$/, "").toUpperCase()}`;
    els.artifacts.appendChild(a);
  }
}

/* ----------------------------- place markers ----------------------------- */

function parseInputCoordinate(text) {
  const m = COORD_RE.exec(text.trim());
  if (!m) return null;
  const lat = parseFloat(m[1]);
  const lon = parseFloat(m[2]);
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { lat, lon };
}

function redrawPlaceMarkers() {
  state.placeMarkers.clearLayers();
  const points = [];
  const originCoord = parseInputCoordinate(els.origin.value);
  const destCoord = parseInputCoordinate(els.destination.value);
  if (originCoord) points.push({ ...originCoord, label: "A", cls: "" });
  viaInputs().forEach((input, idx) => {
    const c = parseInputCoordinate(input.value);
    if (c) points.push({ ...c, label: String(idx + 1), cls: "" });
  });
  if (destCoord) points.push({ ...destCoord, label: "B", cls: "dest" });

  for (const p of points) {
    const icon = L.divIcon({
      className: "place-marker",
      html: `<span class="place-badge ${p.cls}">${p.label}</span>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    L.marker([p.lat, p.lon], { icon }).addTo(state.placeMarkers);
  }
}

/* -------------------------------- helpers -------------------------------- */

function fmtKm(meters) {
  if (meters == null) return "—";
  return `${(meters / 1000).toFixed(1)} km`;
}

function fmtM(meters) {
  if (meters == null) return "—";
  return `${Math.round(meters)} m`;
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const total = Math.round(seconds / 60);
  const h = Math.floor(total / 60);
  const min = total % 60;
  return h > 0 ? `${h} h ${min} min` : `${min} min`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/* --------------------------- wire up the inputs -------------------------- */

function wireEvents() {
  for (const id of ["origin-input", "destination-input"]) {
    $(id).addEventListener("input", () => markCoordInput($(id)));
  }

  document.querySelectorAll(".icon-btn[data-map-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.mapTarget;
      const active = state.pickingTarget === target;
      stopPicking();
      if (!active) startPicking(target, target, btn);
    });
  });

  els.mapModeCancel.addEventListener("click", (ev) => {
    ev.preventDefault();
    stopPicking();
  });

  els.addVia.addEventListener("click", () => addViaRow());
  els.swap.addEventListener("click", swapPlaces);
  els.planBtn.addEventListener("click", planRoute);
  els.resetBtn.addEventListener("click", resetAll);

  for (const input of document.querySelectorAll("#places-panel input, #constraints-panel input, #constraints-panel select")) {
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        planRoute();
      }
    });
  }

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") stopPicking();
  });
}

function resetAll() {
  stopPicking();
  els.origin.value = "";
  els.destination.value = "";
  els.viaList.innerHTML = "";
  els.bikeType.value = "gravel";
  for (const id of ["target-distance", "max-distance", "max-ascent", "prefer-surfaces", "avoid-surfaces"]) {
    $(id).value = "";
  }
  els.avoidTraffic.checked = true;
  els.avoidFerries.checked = true;
  els.returnOrigin.checked = false;
  for (const id of ["origin-input", "destination-input"]) markCoordInput($(id));
  clearResults();
  setStatus("info", "Reset. Enter two places and press <strong>Plan route</strong>.") ;
  setTimeout(() => setStatus(null, ""), 2500);
  state.map.setView([51.75, -1.25], 12);
}

/* --------------------------------- boot ---------------------------------- */

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  wireEvents();
});
