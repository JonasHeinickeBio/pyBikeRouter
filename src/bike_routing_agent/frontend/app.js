/* pyBikeRouter frontend: form -> POST /v1/route/plan -> Leaflet map + results. */
"use strict";

const API_BASE = "";  // same origin

const $ = (id) => document.getElementById(id);

const els = {
  locateOrigin: $("locate-origin"),
  origin: $("origin-input"),
  destination: $("destination-input"),
  viaList: $("via-list"),
  addVia: $("add-via"),
  swap: $("swap"),
  bikeType: $("bike-type"),
  targetDistance: $("target-distance"),
  maxDistance: $("max-distance"),
  maxAscent: $("max-ascent"),
  maxAlternatives: $("max-alternatives"),
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
  viewingNote: $("viewing-note"),
  explanation: $("explanation"),
  metrics: $("metrics"),
  candidatesDetails: $("candidates-details"),
  candidatesTable: $("candidates-table"),
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
  departurePreset: $("departure-preset"),
  departureCustom: $("departure-custom"),
  departureCustomField: $("departure-custom-field"),
  departureHint: $("departure-hint"),
  weatherCard: $("weather-card"),
  textPanel: $("text-panel"),
  textInput: $("text-input"),
  textBtn: $("text-btn"),
};

const COORD_RE = /^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/;

// The selected candidate is drawn in the accent colour; alternatives use a
// distinct palette so users can tell them apart on the map (issue #6).
const SELECTED_COLOR = "#0e7a4a";
const CANDIDATE_COLORS = ["#2563eb", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#65a30d"];

const state = {
  map: null,
  candidateLayers: [], // [{ candidate, layer, color, selected }]
  candidates: [],
  selectedRoute: null,
  activeIndex: 0, // index into candidateLayers currently being inspected
  placeMarkers: L.layerGroup(),
  weatherMarkers: L.layerGroup(), // forecast points along the active candidate
  pickingTarget: null, // "origin" | "destination" | { viaRow: element }
  lastResponse: null,
  aborted: null,
  // Bumped by every location request and by Reset, so a slow geolocation
  // callback that outlives a Reset cannot rewrite the cleared form.
  locationRequest: 0,
};

/* ------------------------------- map setup ------------------------------- */

function initMap() {
  state.map = L.map(els.map, { zoomControl: true }).setView([51.75, -1.25], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(state.map);
  state.placeMarkers.addTo(state.map);
  state.weatherMarkers.addTo(state.map);
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

/* ------------------------- phone / geolocation --------------------------- */

const isPhoneLayout = () => window.matchMedia("(max-width: 800px)").matches;

// On the stacked phone layout the results sit below the map; bring them into
// view so a finished plan is not hidden under the fold.
function revealOnPhone(panel) {
  if (isPhoneLayout()) panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function useMyLocation() {
  // Browsers only expose geolocation on secure origins (https or localhost);
  // over Tailscale that means `tailscale serve` (docs/mobile.md).
  if (!window.isSecureContext) {
    setStatus(
      "error",
      "Location needs a secure (https) connection. Open this app through <code>tailscale serve</code> (see docs/mobile.md).",
    );
    return;
  }
  els.locateOrigin.disabled = true;
  const request = ++state.locationRequest;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      if (request !== state.locationRequest) return;
      els.locateOrigin.disabled = false;
      const { latitude, longitude } = pos.coords;
      els.origin.value = `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`;
      markCoordInput(els.origin);
      redrawPlaceMarkers();
      state.map.setView([latitude, longitude], Math.max(state.map.getZoom(), 14));
      setStatus("", "");
    },
    (err) => {
      if (request !== state.locationRequest) return;
      els.locateOrigin.disabled = false;
      const reason =
        err.code === err.PERMISSION_DENIED
          ? "Location permission was denied. Allow it for this site in iOS Settings."
          : "Could not get your location.";
      setStatus("error", escapeHtml(reason));
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
  );
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

/* ------------------------------ departure time ---------------------------- */

const MAX_FORECAST_DAYS = 14; // matches the API's departure_time limit

/** The chosen departure as a Date, or null for "now" (the server then uses its own clock). */
function departureTime() {
  const preset = els.departurePreset.value;
  const now = new Date();
  if (preset === "1h") return new Date(now.getTime() + 3600 * 1000);
  if (preset === "evening" || preset === "tomorrow") {
    const hour = preset === "evening" ? 18 : 8;
    const when = new Date(now);
    when.setHours(hour, 0, 0, 0);
    // "tomorrow morning" is always the next day; "this evening" rolls over once it has passed
    if (preset === "tomorrow" || when <= now) when.setDate(when.getDate() + 1);
    return when;
  }
  if (preset === "custom") {
    const raw = els.departureCustom.value;
    if (!raw) return null;
    const when = new Date(raw); // datetime-local is read as local time
    return Number.isNaN(when.getTime()) ? null : when;
  }
  return null;
}

function updateDepartureHint() {
  const isCustom = els.departurePreset.value === "custom";
  els.departureCustomField.hidden = !isCustom;
  const when = departureTime();
  const base = `Sets the weather forecast along the route, up to ${MAX_FORECAST_DAYS} days ahead.`;
  if (when === null) {
    els.departureHint.textContent = base;
    return;
  }
  const tooFar = when.getTime() > Date.now() + MAX_FORECAST_DAYS * 86400 * 1000;
  els.departureHint.textContent = tooFar
    ? `That is more than ${MAX_FORECAST_DAYS} days ahead; forecasts do not reach that far.`
    : `Departing ${BikeWeather.dayAndClock(when.toISOString())} your time. ${base}`;
}

function initDeparture() {
  const pad = (n) => String(n).padStart(2, "0");
  const local = (d) =>
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const now = new Date();
  els.departureCustom.min = local(now);
  els.departureCustom.max = local(new Date(now.getTime() + MAX_FORECAST_DAYS * 86400 * 1000));
  els.departurePreset.addEventListener("change", () => {
    if (els.departurePreset.value === "custom" && !els.departureCustom.value) {
      const soon = new Date(now.getTime() + 3600 * 1000);
      soon.setMinutes(0, 0, 0);
      els.departureCustom.value = local(soon);
    }
    updateDepartureHint();
  });
  els.departureCustom.addEventListener("input", updateDepartureHint);
  updateDepartureHint();
}

// Optional cap on distinct alternatives; empty means "every candidate" and
// is omitted from the request so the server default applies (issue #24).
function maxAlternatives() {
  const raw = els.maxAlternatives.value.trim();
  if (raw === "") return null;
  const n = Math.trunc(Number(raw));
  return Number.isFinite(n) ? Math.min(5, Math.max(1, n)) : null;
}

function buildRequest() {
  const request = {
    origin: placeValue(els.origin.value),
    destination: placeValue(els.destination.value),
    via: viaInputs().map((i) => placeValue(i.value)).filter((v) => v !== ""),
    constraints: buildConstraints(),
  };
  const alternatives = maxAlternatives();
  if (alternatives !== null) request.max_alternatives = alternatives;
  const departure = departureTime();
  if (departure !== null) request.departure_time = departure.toISOString();
  return request;
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

/* --------------------------- plan from plain words ------------------------ */

/** Show the description box only when the server has a language-model parser configured. */
async function initTextPlanning() {
  try {
    const resp = await fetch(`${API_BASE}/v1/capabilities`);
    if (!resp.ok) return;
    const caps = await resp.json();
    els.textPanel.hidden = !caps.text_planning;
  } catch {
    // Older server or offline: the form works exactly as before.
  }
}

/** Put what the server understood into the form, so it can be checked and re-planned by hand. */
function applyInterpretation(interpretation) {
  const v = BikeText.formValues(interpretation);
  if (!v) return;
  els.origin.value = v.origin;
  els.destination.value = v.destination;
  els.viaList.innerHTML = "";
  for (const place of v.via) addViaRow(place);
  if (v.bikeType) els.bikeType.value = v.bikeType;
  const setNum = (el, value) => {
    if (value !== null) el.value = String(value);
  };
  setNum(els.targetDistance, v.targetDistance);
  setNum(els.maxDistance, v.maxDistance);
  setNum(els.maxAscent, v.maxAscent);
  if (v.prefer) els.preferSurfaces.value = v.prefer;
  if (v.avoid) els.avoidSurfaces.value = v.avoid;
  if (v.avoidTraffic !== null) els.avoidTraffic.checked = v.avoidTraffic;
  if (v.avoidFerries !== null) els.avoidFerries.checked = v.avoidFerries;
  if (v.loop !== null) els.returnOrigin.checked = v.loop;
  if (v.departureLocal) {
    els.departurePreset.value = "custom";
    els.departureCustom.value = v.departureLocal;
  }
  updateDepartureHint();
  for (const input of [els.origin, els.destination, ...viaInputs()]) markCoordInput(input);
  redrawPlaceMarkers();
}

async function planFromText() {
  stopPicking();
  const text = els.textInput.value.trim();
  if (!text) {
    setStatus("error", "Describe the ride you want first.");
    return;
  }
  const payload = { text, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone };
  const maxAlt = els.maxAlternatives.value.trim();
  if (maxAlt !== "") payload.max_alternatives = Number(maxAlt);

  els.textBtn.disabled = true;
  els.planBtn.disabled = true;
  setStatus("loading", "Reading your description and planning&hellip;");
  clearResults();

  if (state.aborted) state.aborted.abort();
  const controller = new AbortController();
  state.aborted = controller;
  const startedAt = performance.now();

  try {
    const resp = await fetch(`${API_BASE}/v1/route/plan-text`, {
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
    if (data.interpretation) applyInterpretation(data.interpretation);
    const secs = ((performance.now() - startedAt) / 1000).toFixed(1);
    renderResponse(data, secs);
    if (data.interpretation && data.status !== "invalid") {
      const summary = BikeText.summaryHtml(data.interpretation);
      if (summary) els.status.insertAdjacentHTML("beforeend", `<div class="interpretation">${summary}</div>`);
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    setStatus("error", `Network error: ${escapeHtml(String(err))}`);
  } finally {
    els.textBtn.disabled = false;
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
  for (const entry of state.candidateLayers) {
    state.map.removeLayer(entry.layer);
  }
  state.candidateLayers = [];
  state.candidates = [];
  state.selectedRoute = null;
  state.activeIndex = 0;
  state.weatherMarkers.clearLayers();
  els.weatherCard.hidden = true;
  els.weatherCard.innerHTML = "";
  els.viewingNote.hidden = true;
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
  revealOnPhone(els.clarificationPanel);
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

  state.selectedRoute = route;
  const list = Array.isArray(data.candidates) && data.candidates.length ? data.candidates : [route];
  const selectedIndex = findCandidateIndex(list, route);
  state.candidates = list;
  state.activeIndex = selectedIndex;

  state.candidateLayers = list.map((candidate, i) => ({
    candidate,
    layer: L.geoJSON(candidate.geometry_geojson, { style: candidateStyle(i) }).addTo(state.map),
    color: colorForIndex(i, selectedIndex),
    selected: i === selectedIndex,
  }));
  state.candidateLayers[selectedIndex].layer.bringToFront();

  const allBounds = boundsOfAllCandidates();
  if (allBounds) state.map.fitBounds(allBounds.pad(0.12));

  redrawPlaceMarkers();

  els.resultsPanel.hidden = false;
  revealOnPhone(els.resultsPanel);
  els.explanation.textContent = data.explanation || "";

  renderCandidatesTable();
  updateActiveView();
  renderArtifacts(data.artifacts);

  const provBits = [];
  if (route.provider) provBits.push(`provider: ${route.provider}`);
  const prov = route.provenance || {};
  for (const key of ["requested_at", "profile", "profile_mapping"]) {
    if (prov[key] !== undefined) provBits.push(`${key}: ${JSON.stringify(prov[key])}`);
  }
  els.provenance.textContent = provBits.join(" · ");
}

/* --------------------------- candidate comparison ------------------------ */

function findCandidateIndex(candidates, route) {
  const routeGeo = JSON.stringify(route.geometry_geojson);
  const idx = candidates.findIndex(
    (c) => c.provider === route.provider && JSON.stringify(c.geometry_geojson) === routeGeo,
  );
  return idx >= 0 ? idx : 0;
}

function colorForIndex(index, selectedIndex) {
  if (index === selectedIndex) return SELECTED_COLOR;
  const ordinal = index < selectedIndex ? index : index - 1;
  return CANDIDATE_COLORS[ordinal % CANDIDATE_COLORS.length];
}

function candidateStyle(index) {
  const entry = state.candidateLayers[index];
  if (!entry) return { color: SELECTED_COLOR, weight: 5, opacity: 0.85 };
  const isActive = index === state.activeIndex;
  return {
    color: entry.color,
    weight: isActive ? 5 : 2.5,
    opacity: isActive ? 0.85 : 0.5,
  };
}

function boundsOfAllCandidates() {
  let bounds = null;
  for (const entry of state.candidateLayers) {
    const b = entry.layer.getBounds();
    if (!b.isValid()) continue;
    bounds = bounds ? bounds.extend(b) : b;
  }
  return bounds && bounds.isValid() ? bounds : null;
}

function activateCandidate(index) {
  if (!state.candidateLayers[index]) return;
  state.activeIndex = index;
  state.candidateLayers.forEach((entry, i) => entry.layer.setStyle(candidateStyle(i)));
  state.candidateLayers[index].layer.bringToFront();
  els.candidatesTable.querySelectorAll("tr.cand-row").forEach((tr, i) => {
    tr.classList.toggle("active", i === index);
  });
  updateActiveView();
}

function windCellHtml(candidate) {
  const cell = BikeWeather.windCell(candidate.weather);
  return `<span class="wind-cell ${cell.kind}" title="${escapeHtml(cell.title)}">${escapeHtml(cell.text)}</span>`;
}

function renderCandidatesTable() {
  if (state.candidates.length < 2) {
    els.candidatesDetails.hidden = true;
    return;
  }
  els.candidatesDetails.hidden = false;
  els.candidatesDetails.open = true;

  const selectedIndex = findCandidateIndex(state.candidates, state.selectedRoute);
  const head =
    "<tr><th class='num'>Rank</th><th>Provider</th><th>Profile</th><th class='num'>Distance</th><th class='num'>Time</th>" +
    "<th class='num'>Ascent</th><th class='num' title='Average wind against (&#9650;) or with (&#9660;) the direction of travel, km/h'>Wind</th>" +
    "<th class='num'>Score</th><th class='num'>Warnings</th></tr>";
  const rows = state.candidates
    .map((cand, i) => {
      const m = cand.metrics || {};
      const warnTitle = cand.warnings && cand.warnings.length
        ? escapeHtml(cand.warnings.join("; "))
        : "No warnings";
      const selectedBadge = i === selectedIndex ? " <span class='badge'>selected</span>" : "";
      const similar = (cand.duplicates || []).length
        ? ` <span class="similar" title="${escapeHtml("Near-identical routes merged into this one: " + cand.duplicates.join(", "))}">+${cand.duplicates.length} similar</span>`
        : "";
      const copyOf = cand.duplicate_of
        ? ` <span class="similar" title="${escapeHtml("Near-identical to " + cand.duplicate_of)}">near-copy</span>`
        : "";
      const active = i === state.activeIndex;
      const rankTitle = cand.rank_rationale ? escapeHtml(cand.rank_rationale) : "";
      const rankCell = cand.rank != null
        ? `<span title="${rankTitle}">${cand.rank}</span>`
        : "—";
      return (
        `<tr class="cand-row${active ? " active" : ""}" data-index="${i}" tabindex="0"` +
        `${rankTitle ? ` title="${rankTitle}"` : ""}>` +
        `<td class="num">${rankCell}</td>` +
        `<td><span class="cand-dot" style="background:${state.candidateLayers[i].color}"></span>${escapeHtml(cand.provider)}${selectedBadge}${similar}${copyOf}</td>` +
        `<td>${escapeHtml(cand.provider_profile || "—")}</td>` +
        `<td class="num">${fmtKm(m.distance_m)}</td>` +
        `<td class="num">${fmtDuration(m.duration_s)}</td>` +
        `<td class="num">${fmtM(m.ascent_m)}</td>` +
        `<td class="num">${windCellHtml(cand)}</td>` +
        `<td class="num">${cand.score != null ? cand.score.toFixed(2) : "—"}</td>` +
        `<td class="num"><span class="warn-count" title="${warnTitle}">${(cand.warnings || []).length || 0}</span></td>` +
        "</tr>"
      );
    })
    .join("");
  els.candidatesTable.innerHTML = head + rows;

  els.candidatesTable.querySelectorAll("tr.cand-row").forEach((tr) => {
    const i = Number(tr.dataset.index);
    tr.addEventListener("click", () => activateCandidate(i));
    tr.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        activateCandidate(i);
      }
    });
  });
}

function updateActiveView() {
  const entry = state.candidateLayers[state.activeIndex];
  if (!entry) return;
  const cand = entry.candidate;
  const m = cand.metrics || {};
  const metric = (value, label) =>
    `<div class="metric"><div class="value">${value}</div><div class="label">${label}</div></div>`;
  els.metrics.innerHTML =
    metric(fmtKm(m.distance_m), "Distance") +
    metric(fmtDuration(m.duration_s), "Time") +
    metric(fmtM(m.ascent_m), "Ascent") +
    metric(fmtM(m.descent_m), "Descent") +
    metric(cand.score != null ? cand.score.toFixed(2) : "—", "Score") +
    metric(escapeHtml(cand.provider_profile || "—"), "Profile");

  renderWeather(cand);
  renderScoreBreakdown(cand.score_breakdown);
  renderSurfaces(m.surface_coverage, m.unknown_surface_fraction);

  els.warnings.innerHTML = (cand.warnings || [])
    .map((w) => `<li>${escapeHtml(w)}</li>`)
    .join("");

  const selectedIndex = findCandidateIndex(state.candidates, state.selectedRoute);
  if (state.activeIndex === selectedIndex) {
    els.viewingNote.hidden = true;
  } else {
    els.viewingNote.hidden = false;
    els.viewingNote.innerHTML =
      `Showing <strong>${escapeHtml(cand.provider)}</strong> &mdash; the explanation and export ` +
      `files below refer to the selected candidate (<strong>${escapeHtml(state.selectedRoute.provider)}</strong>).`;
  }
}

/* ---------------------------------- weather ------------------------------- */

function renderWeather(candidate) {
  const status = state.lastResponse ? state.lastResponse.weather_status : null;
  const html = BikeWeather.weatherCardHtml(candidate.weather, status);
  els.weatherCard.innerHTML = html;
  els.weatherCard.hidden = html === "";

  // Forecast points on the map for the candidate being inspected, so the
  // numbers in the card can be tied to a place along the route.
  state.weatherMarkers.clearLayers();
  if (!candidate.weather) return;
  for (const sample of candidate.weather.samples || []) {
    const info = BikeWeather.conditionInfo(sample.weather.condition, sample.weather.is_day);
    const icon = L.divIcon({
      className: "wx-marker-wrap",
      html: `<span class="wx-marker" aria-hidden="true">${info.icon}</span>`,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
    L.marker([sample.lat, sample.lon], { icon, keyboard: false })
      .bindTooltip(BikeWeather.sampleTooltip(sample), { direction: "top", offset: [0, -12] })
      .addTo(state.weatherMarkers);
  }
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
    a.textContent = `\u2B07 ${key.replace(/_url$/, "").toUpperCase()}`;
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

  if ("geolocation" in navigator) {
    els.locateOrigin.hidden = false;
    els.locateOrigin.addEventListener("click", useMyLocation);
  }

  els.mapModeCancel.addEventListener("click", (ev) => {
    ev.preventDefault();
    stopPicking();
  });

  els.addVia.addEventListener("click", () => addViaRow());
  els.swap.addEventListener("click", swapPlaces);
  els.planBtn.addEventListener("click", planRoute);
  els.textBtn.addEventListener("click", planFromText);
  els.textInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      planFromText();
    }
  });
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
  state.locationRequest++; // drop any location lookup still in flight
  els.locateOrigin.disabled = false;
  stopPicking();
  els.origin.value = "";
  els.destination.value = "";
  els.viaList.innerHTML = "";
  els.bikeType.value = "gravel";
  for (const id of ["target-distance", "max-distance", "max-ascent", "max-alternatives", "prefer-surfaces", "avoid-surfaces"]) {
    $(id).value = "";
  }
  els.departurePreset.value = "now";
  els.departureCustom.value = "";
  updateDepartureHint();
  els.avoidTraffic.checked = true;
  els.avoidFerries.checked = true;
  els.returnOrigin.checked = false;
  els.textInput.value = "";
  for (const id of ["origin-input", "destination-input"]) markCoordInput($(id));
  clearResults();
  setStatus("info", "Reset. Enter two places and press <strong>Plan route</strong>.") ;
  setTimeout(() => setStatus(null, ""), 2500);
  state.map.setView([51.75, -1.25], 12);
}

/* --------------------------------- boot ---------------------------------- */

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initDeparture();
  wireEvents();
  initTextPlanning();
});
