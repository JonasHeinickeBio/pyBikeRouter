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
  copyLinkBtn: $("copy-link-btn"),
  importBtn: $("import-btn"),
  importInput: $("import-input"),
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
  alternatives: $("alternatives"),
  alternativesList: $("alternatives-list"),
  altDownload: $("alt-download"),
  altDownloadLabel: $("alt-download-label"),
  textPanel: $("text-panel"),
  textInput: $("text-input"),
  textBtn: $("text-btn"),
  poiPanel: $("poi-panel"),
  poiEnabled: $("poi-enabled"),
  poiCategories: $("poi-categories"),
  poiStatus: $("poi-status"),
  poiSettings: $("poi-settings"),
  settingsToggle: $("settings-toggle"),
  settingsPanel: $("settings-panel"),
  settingsClose: $("settings-close"),
  settingsEmpty: $("settings-empty"),
  memoryEnabled: $("memory-enabled"),
  memoryKinds: $("memory-kinds"),
  memoryStatus: $("memory-status"),
  memoryClear: $("memory-clear"),
  poiStopsEnabled: $("poi-stops-enabled"),
  poiStopsField: $("poi-stops-field"),
  poiStopsCount: $("poi-stops-count"),
  poiStopsHint: $("poi-stops-hint"),
  poiStopsNote: $("poi-stops-note"),
  coverageCard: $("coverage-card"),
  chatPanel: $("chat-panel"),
  chatLog: $("chat-log"),
  chatChips: $("chat-chips"),
  chatForm: $("chat-form"),
  chatInput: $("chat-input"),
  chatSend: $("chat-send"),
  modeTabs: $("mode-tabs"),
  optimizePresets: $("optimize-presets"),
  optimizeGoals: $("optimize-goals"),
  optimizeDetour: $("optimize-detour"),
  optimizeEffort: $("optimize-effort"),
  optimizeDetours: $("optimize-detours"),
  profileCard: $("profile-card"),
  coloursCard: $("colours-card"),
  reshapeHint: $("reshape-hint"),
  coloursModes: $("colours-modes"),
  coloursLegend: $("colours-legend"),
  coloursLongest: $("colours-longest"),
  coloursNote: $("colours-note"),
  traceCard: $("trace-card"),
  traceSummary: $("trace-summary"),
  traceBody: $("trace-body"),
  cuesCard: $("cues-card"),
  cuesSummary: $("cues-summary"),
  cuesList: $("cues-list"),
  cuesDownload: $("cues-download"),
  profileChart: $("profile-chart"),
  profileHover: $("profile-hover"),
  profileFacts: $("profile-facts"),
  profileLegend: $("profile-legend"),
  serviceStatus: $("service-status"),
  serviceStatusText: $("service-status-text"),
  serviceStatusPanel: $("service-status-panel"),
  serviceStatusSummary: $("service-status-summary"),
  serviceStatusList: $("service-status-list"),
  serviceStatusChecked: $("service-status-checked"),
  chatNew: $("chat-new"),
  chatEarlierBox: $("chat-earlier-box"),
  chatEarlier: $("chat-earlier"),
  chatEarlierEmpty: $("chat-earlier-empty"),
  optimizePoiOptions: $("optimize-poi-options"),
  optimizePoiCorridor: $("optimize-poi-corridor"),
  optimizePoiFame: $("optimize-poi-fame"),
  optimizeHint: $("optimize-hint"),
  optimizeBtn: $("optimize-btn"),
  optimizeResetBtn: $("optimize-reset-btn"),
  optimizationPanel: $("optimization-panel"),
  optimizationReport: $("optimization-report"),
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
  pointLayer: L.layerGroup(), // the point picked by a click, with its menu
  profileLayer: L.layerGroup(), // where the elevation chart's cursor is on the route
  cueLayer: L.layerGroup(), // the step picked in the directions list
  colourLayer: L.layerGroup(), // the active route cut into surface / traffic colours
  provisionalLayer: L.layerGroup(), // the best route, drawn while the rest of the plan is still made
  reshapeLayer: L.layerGroup(), // the handle that follows the pointer over the route and can be dragged
  reshapeMarker: null,
  reshapeHideTimer: null,
  reshapeDragging: false,
  reshapeSticky: false, // a long press placed the handle: it stays until the next tap instead of following a mouse
  memory: { enabled: false, keep: {}, values: {} }, // BikeMemory state, loaded in initMemory
  memoryStorageFailed: false, // the last save was refused by the browser
  memoryHoldBack: false, // the form holds a shared link's trip: not remembered until the person edits it
  colourMode: "none", // "none" | "surface" | "traffic" (remembered in this browser)
  cueRows: [], // the rows of the directions list shown
  profile: null, // the profile of the route shown
  profileMarker: null,
  ignoreMapClickUntil: 0, // a click before this time is the tail of a drag, not a choice of point
  foreignPopupClosedAt: 0, // when a popup that is not the point menu was last closed by a click
  weatherMarkers: L.layerGroup(), // forecast points along the active candidate
  poiLayer: L.layerGroup(), // points of interest (along the active route, or in the map view)
  poiStopLayer: L.layerGroup(), // the famous sights the plan was routed past
  importLayer: L.layerGroup(), // the start, finish and waypoints of an imported GPX track
  poiCategories: [], // [{key, label, kind}] from the server
  poiPrefs: { enabled: true, keys: [] },
  poiTimezone: null, // IANA zone the server named for the active route's sights (opening hours)
  poiRequest: 0, // bumped by every refresh and clear, so a slow answer cannot redraw stale POIs
  poiTimer: null,
  engineOverride: null, // engines for the next plan only (set by "plan with openrouteservice")
  segmentJob: null, // the tile download being followed
  chatSession: null, // the server-side conversation (kept in memory there)
  chatBusy: false,
  formRouteUnsent: false, // the route on screen came from the form and the chat was not told
  formDirty: false, // the form was edited since the chat last heard from it
  planGeneration: 0, // bumped by every form plan and every drawn chat plan; a stale response is dropped
  planPending: null, // generation of the form/text request in flight, if any
  renderedGeneration: 0, // generation of the plan on the map
  gpxImportRequest: 0, // the latest GPX file chosen or dropped
  deferredChat: null, // a chat plan that arrived while a newer form request was running
  capsPromise: null,
  pickKeys: new Set(), // downloads already reported to the pick log (opt-in, PICK_LOG_PATH)
  tab: "plan", // "plan" | "optimize" (the Optimize tab exists when capabilities.optimizer)
  optimizeGoals: {}, // metric -> "high" | "medium" | "low" for the goals that are ticked
  optimizeCaps: { pois: false },
  optimizing: null, // the timer of a running optimisation
  pickingTarget: null, // "origin" | "destination" | { viaRow: element }
  lastResponse: null,
  aborted: null,
  // Bumped by every location request and by Reset, so a slow geolocation
  // callback that outlives a Reset cannot rewrite the cleared form.
  locationRequest: 0,
};

/* ------------------------------- map setup ------------------------------- */

function initMap() {
  // tapHold: a long press is a `contextmenu` on every touch browser (Leaflet does it for iOS only by
  // default); it is how a finger asks for the route's drag handle.
  state.map = L.map(els.map, { zoomControl: true, tapHold: true }).setView([51.75, -1.25], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(state.map);
  state.placeMarkers.addTo(state.map);
  state.pointLayer.addTo(state.map);
  state.profileLayer.addTo(state.map);
  state.cueLayer.addTo(state.map);
  state.colourLayer.addTo(state.map);
  state.provisionalLayer.addTo(state.map);
  state.reshapeLayer.addTo(state.map);
  state.colourMode = loadColourMode();
  state.weatherMarkers.addTo(state.map);
  state.poiLayer.addTo(state.map);
  state.poiStopLayer.addTo(state.map);
  state.importLayer.addTo(state.map);
  state.map.on("click", onMapClick);
  state.map.getContainer().addEventListener("contextmenu", onLongPress);
  // A click that only closes another popup (a sight, a weather marker) must not open the menu.
  state.map.on("popupclose", (ev) => {
    if (!ev.popup || ev.popup.options.className !== "point-popup") {
      state.foreignPopupClosedAt = performance.now();
    }
  });
  state.map.on("moveend", () => {
    if (!state.candidateLayers.length) schedulePoiRefresh();
  });
}

const DRAG_CLICK_GUARD_MS = 350;

/** A drag that ends over a map control (zoom buttons, a popup) makes the browser send a click to the
 * map, which would open the point menu: swallow the click that follows a drag. */
function guardClickAfterDrag() {
  state.ignoreMapClickUntil = performance.now() + DRAG_CLICK_GUARD_MS;
}

function onMapClick(ev) {
  if (performance.now() < state.ignoreMapClickUntil) return;
  if (state.reshapeSticky) {
    hideReshapeHandle(); // a tap away from a long-pressed handle only drops it, it picks no point
    return;
  }
  if (!state.pickingTarget) {
    if (performance.now() - state.foreignPopupClosedAt > 80) openPointMenu(ev.latlng);
    return;
  }
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

/* ------------------------ a point picked on the map ------------------------ */

/** What the place fields hold now, as the placement logic wants it. */
function currentPlaces() {
  const vias = viaInputs();
  return {
    hasOrigin: els.origin.value.trim() !== "",
    hasDestination: els.destination.value.trim() !== "",
    origin: parseInputCoordinate(els.origin.value),
    destination: parseInputCoordinate(els.destination.value),
    vias: vias.map((input) => parseInputCoordinate(input.value)),
    viaCount: vias.length,
  };
}

/** The route on the map ([lon, lat] pairs), or null. */
function activeRouteLine() {
  const entry = state.candidateLayers[state.activeIndex];
  if (entry && entry.candidate.provider === "gpx") return null; // dragging cannot reshape a file's track
  const coords = entry && entry.candidate && entry.candidate.geometry_geojson
    ? entry.candidate.geometry_geojson.coordinates
    : null;
  return Array.isArray(coords) && coords.length >= 2 && typeof coords[0][0] === "number" ? coords : null;
}

function closePointMenu() {
  state.pointLayer.clearLayers();
}

/** Select the clicked point and offer where in the trip to put it. */
function openPointMenu(latlng) {
  closePointMenu();
  const ll = latlng.wrap();
  const text = BikeWaypoints.coordText(ll.lat, ll.lng);
  const places = currentPlaces();
  const options = BikeWaypoints.slots(places.viaCount, places);
  const best = BikeWaypoints.suggest({
    ...places,
    route: activeRouteLine(),
    point: { lat: ll.lat, lon: ll.lng },
  });

  const menu = document.createElement("div");
  menu.className = "point-menu";
  const coords = document.createElement("div");
  coords.className = "point-coords";
  coords.textContent = text;
  const label = document.createElement("label");
  label.textContent = "Add this point as";
  label.htmlFor = "point-slot";
  const select = document.createElement("select");
  select.id = "point-slot";
  for (const option of options) {
    const el = document.createElement("option");
    el.value = option.key;
    el.textContent = option.label;
    select.appendChild(el);
  }
  select.value = options.some((o) => o.key === best) ? best : "end";
  const actions = document.createElement("div");
  actions.className = "point-actions";
  const button = (caption, className, handler) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = className;
    b.textContent = caption;
    b.addEventListener("click", handler);
    return b;
  };
  actions.append(
    button("Add", "primary", () => applyPoint(select.value, text, false)),
    button("Add & plan", "", () => applyPoint(select.value, text, true)),
    button("Cancel", "link", closePointMenu),
  );
  menu.append(coords, label, select, actions);
  L.DomEvent.disableClickPropagation(menu);

  const marker = L.marker(ll, {
    icon: L.divIcon({ className: "", html: '<span class="point-pin"></span>', iconSize: [18, 18], iconAnchor: [9, 9] }),
    keyboard: false,
  }).addTo(state.pointLayer);
  marker.bindPopup(menu, { className: "point-popup", minWidth: 230, offset: [0, -4] });
  marker.on("popupclose", closePointMenu);
  marker.openPopup();
  select.focus({ preventScroll: true });
}

/* ----------------------------- drag to reshape ----------------------------- */

const RESHAPE_MIN_DRAG_PX = 25; // a shorter drag is a slip, not a new stop
const RESHAPE_HIDE_MS = 350;

function hideReshapeHandle() {
  clearTimeout(state.reshapeHideTimer);
  state.reshapeLayer.clearLayers();
  state.reshapeMarker = null;
  state.reshapeDragging = false;
  state.reshapeSticky = false;
}

function scheduleReshapeHide() {
  clearTimeout(state.reshapeHideTimer);
  if (state.reshapeDragging || state.reshapeSticky) return;
  state.reshapeHideTimer = setTimeout(hideReshapeHandle, RESHAPE_HIDE_MS);
}

/** The handle follows the pointer along the active route; drag it to pull the route through a new place. */
function showReshapeHandle(latlng) {
  if (state.reshapeDragging) return;
  clearTimeout(state.reshapeHideTimer);
  if (state.reshapeMarker) {
    state.reshapeMarker.setLatLng(latlng);
    return;
  }
  const marker = L.marker(latlng, {
    draggable: true,
    keyboard: false,
    bubblingMouseEvents: true, // a plain click on the route still reaches the map's point menu
    zIndexOffset: 900,
    title: "Drag to reshape the route",
    icon: L.divIcon({ className: "", html: '<span class="reshape-handle"></span>', iconSize: [18, 18], iconAnchor: [9, 9] }),
  }).addTo(state.reshapeLayer);
  let grab = null;
  let neighbours = null;
  let preview = null;
  marker.on("dragstart", () => {
    state.reshapeDragging = true;
    clearTimeout(state.reshapeHideTimer);
    grab = marker.getLatLng();
    const g = grab.wrap();
    neighbours = BikeWaypoints.reshapeNeighbours({
      ...currentPlaces(),
      route: activeRouteLine(),
      grab: { lat: g.lat, lon: g.lng },
    });
    preview = L.polyline([], { color: "#0e7a4a", weight: 4, opacity: 0.9, dashArray: "6 8", interactive: false }).addTo(state.reshapeLayer);
  });
  // While dragging: the route will pass from the stop before, through the handle, to the stop
  // after it (or just from where it was grabbed, when those are only names).
  marker.on("drag", () => {
    if (!preview) return;
    const here = marker.getLatLng();
    const path = [];
    if (neighbours && neighbours.before) path.push(neighbours.before);
    path.push(here);
    if (neighbours && neighbours.after) path.push(neighbours.after);
    preview.setLatLngs(path.length > 1 ? path : [grab, here]);
  });
  marker.on("dragend", () => {
    guardClickAfterDrag();
    const drop = marker.getLatLng();
    const moved = grab ? state.map.latLngToContainerPoint(grab).distanceTo(state.map.latLngToContainerPoint(drop)) : 0;
    const from = grab;
    hideReshapeHandle();
    if (!from || moved < RESHAPE_MIN_DRAG_PX) return;
    reshapeThrough(from, drop.wrap());
  });
  marker.on("mouseover", () => clearTimeout(state.reshapeHideTimer));
  marker.on("mouseout", scheduleReshapeHide);
  state.reshapeMarker = marker;
}

/** Route the trip through `drop`, as a stop placed where the route was grabbed at `grab`. */
function reshapeThrough(grab, drop) {
  const g = grab.wrap();
  const placement = BikeWaypoints.reshape({
    ...currentPlaces(),
    route: activeRouteLine(),
    grab: { lat: g.lat, lon: g.lng },
    drop: { lat: drop.lat, lon: drop.lng },
  });
  if (!placement) {
    setStatus("error", "Add a start and an end first (and at most " + BikeWaypoints.MAX_VIA + " stops fit) to reshape the route by dragging it.");
    return;
  }
  applyPoint(placement.key, placement.text, true);
}

const LONG_PRESS_REACH_PX = 28; // a finger lands this close to a line, not on it

/**
 * Touch: no hovering, so a long press near the active route puts the handle on the route and leaves
 * it. A plain DOM listener, not a Leaflet one: Leaflet cancels the browser's menu for every
 * `contextmenu` it hands to a listener, which would take a mouse's right click away. Only a
 * simulated long press (Leaflet's tapHold) or a touch/pen contextmenu counts; a mouse's does not.
 */
function onLongPress(ev) {
  const touch = ev._simulated || (ev.pointerType && ev.pointerType !== "mouse");
  if (!touch || state.pickingTarget) return;
  const line = activeRouteLine();
  if (!line) return;
  const at = BikeWaypoints.nearestOnRoute(
    line,
    state.map.mouseEventToContainerPoint(ev),
    (lon, lat) => state.map.latLngToContainerPoint([lat, lon]),
    LONG_PRESS_REACH_PX,
  );
  if (!at) return;
  ev.preventDefault();
  hideReshapeHandle();
  state.reshapeSticky = true;
  showReshapeHandle(L.latLng(at.lat, at.lon));
  state.reshapeSticky = true; // showReshapeHandle must not be able to clear it
}

/** Make a route line show the drag handle while it is the active one; returns the layer. */
function attachReshape(layer, index) {
  layer.on("mousemove", (ev) => {
    if (index !== state.activeIndex || state.pickingTarget || !activeRouteLine()) return;
    showReshapeHandle(ev.latlng);
  });
  layer.on("mouseout", scheduleReshapeHide);
  return layer;
}

/** Put the picked point into the place fields at `key` and, if asked, plan again. */
function applyPoint(key, text, andPlan) {
  const placed = BikeWaypoints.place(
    {
      origin: els.origin.value,
      destination: els.destination.value,
      vias: viaInputs().map((input) => input.value),
    },
    key,
    text,
  );
  if (!placed) {
    setStatus("error", "No more stops fit: a trip has at most " + BikeWaypoints.MAX_VIA + ".");
    return;
  }
  stopPicking();
  if (key === "start") els.origin.value = text;
  else if (key === "end") els.destination.value = text;
  else insertViaRow(Number(key.split(":")[1]), text);
  for (const input of [els.origin, els.destination, ...viaInputs()]) markCoordInput(input);
  closePointMenu();
  redrawPlaceMarkers();
  if (andPlan) {
    planRoute();
    return;
  }
  const drawn = state.candidateLayers.length > 0;
  setStatus("info", `Added as ${placed.label}.` + (drawn ? " Press <strong>Plan route</strong> to use it." : ""));
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
  hideReshapeHandle(); // the first tap of a picker must pick, not just drop a long-pressed handle
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
  return row;
}

/** A via row at position `index` (0 = first stop after the start). */
function insertViaRow(index, value) {
  const row = addViaRow(value);
  const before = els.viaList.children[index];
  if (before && before !== row) els.viaList.insertBefore(row, before);
  return row;
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
  const stops = poiStopsRequest();
  if (stops !== null) request.poi_stops = stops;
  if (state.engineOverride) {
    request.routing_engines = state.engineOverride;
    state.engineOverride = null; // for this plan only
  }
  return BikeOptimize.forLoop(request);
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
  if (!payload.constraints.return_to_origin && typeof payload.destination !== "object" && payload.destination === "") {
    setStatus("error", "Please enter a destination (or tick \"Return to origin\" for a loop).");
    return;
  }

  els.planBtn.disabled = true;
  setStatus("loading", "Planning route&hellip; <em>geocoding and routing can take a few seconds</em>");
  clearResults();

  if (state.aborted) state.aborted.abort();
  const controller = new AbortController();
  state.aborted = controller;
  const generation = ++state.planGeneration;
  state.planPending = generation;
  let drawn = false;
  const startedAt = performance.now();

  try {
    const outcome = await fetchPlan(payload, controller.signal, () => generation === state.planGeneration);
    if (outcome.failed) {
      setStatus("error", outcome.failed);
      return;
    }
    const data = outcome.data;
    if (generation !== state.planGeneration) return; // a newer plan (form or chat) took over
    state.lastResponse = data;
    const secs = ((performance.now() - startedAt) / 1000).toFixed(1);
    renderResponse(data, secs);
    drawn = true;
    if (!state.memoryHoldBack) rememberNow(); // a plan that worked: its choices are what to remember
  } catch (err) {
    if (err.name === "AbortError") return;
    setStatus("error", `Network error: ${escapeHtml(String(err))}`);
  } finally {
    els.planBtn.disabled = false;
    if (!drawn) state.provisionalLayer.clearLayers();
    settlePlan(generation, drawn);
  }
}

/**
 * Ask for a plan and wait for it. The stream endpoint tells the steps as they happen (the status
 * line says which one is running) and the best route as soon as it exists (drawn faintly until the
 * whole plan arrives); a server without it (older version) is asked the plain way. Resolves with
 * `{data}` (the same body either way) or `{failed}` (the text to show).
 */
async function fetchPlan(payload, signal, isCurrent) {
  const streamed = await streamPlan(payload, signal, isCurrent);
  if (!streamed.unsupported) return streamed;
  const resp = await fetch(`${API_BASE}/v1/route/plan?trace=true`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!resp.ok) {
    return { failed: `Request failed (HTTP ${resp.status}): ${escapeHtml(await safeErrorText(resp))}` };
  }
  return { data: await resp.json() };
}

async function streamPlan(payload, signal, isCurrent) {
  const resp = await fetch(`${API_BASE}/v1/route/plan/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (resp.status === 404 || resp.status === 405 || (resp.ok && !resp.body)) return { unsupported: true };
  if (!resp.ok) {
    return { failed: `Request failed (HTTP ${resp.status}): ${escapeHtml(await safeErrorText(resp))}` };
  }
  const parser = BikeStream.createParser();
  const decoder = new TextDecoder();
  const reader = resp.body.getReader();
  let result = null;
  let error = null;
  let fitted = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const { event, data } of parser.push(decoder.decode(value, { stream: true }))) {
      if (event === "result") result = data;
      else if (event === "error") error = data && data.detail;
      else if (!isCurrent()) continue; // a newer plan owns the screen
      else if (event === "step") setStatus("loading", `${escapeHtml(BikeStream.progressText(data))}`);
      else if (event === "route") {
        const line = BikeStream.provisionalLine(data);
        if (!line) continue;
        state.provisionalLayer.clearLayers();
        const drawn = L.polyline(line, { color: "#0e7a4a", weight: 5, opacity: 0.65, dashArray: "2 8", interactive: false });
        drawn.addTo(state.provisionalLayer);
        if (!fitted) {
          state.map.fitBounds(drawn.getBounds(), { padding: [30, 30] });
          fitted = true;
        }
      }
    }
  }
  if (result) return { data: result };
  return { failed: escapeHtml(typeof error === "string" ? error : "The connection closed before the plan was finished.") };
}

/** A form/text request is over: remember what is on the map; if it failed, the chat's route
 * that arrived meanwhile (and was held back) is shown after all. */
function settlePlan(generation, drawn) {
  if (state.planPending === generation) state.planPending = null;
  if (generation !== state.planGeneration) return; // a newer plan took over
  if (drawn) {
    state.renderedGeneration = generation;
    state.deferredChat = null;
  } else if (state.deferredChat) {
    const held = state.deferredChat;
    state.deferredChat = null;
    chatSay("bot", "(The form request did not give a route, so here is the one from our chat.)");
    drawChatPlan(held.plan, held.focusRank, held.secs, held.request);
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
  const generation = ++state.planGeneration;
  state.planPending = generation;
  let drawn = false;
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
    if (generation !== state.planGeneration) return; // a newer plan (form or chat) took over
    state.lastResponse = data;
    if (data.interpretation) applyInterpretation(data.interpretation);
    const secs = ((performance.now() - startedAt) / 1000).toFixed(1);
    renderResponse(data, secs);
    drawn = true;
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
    settlePlan(generation, drawn);
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
  state.poiLayer.clearLayers();
  state.poiStopLayer.clearLayers();
  state.importLayer.clearLayers();
  state.poiRequest++;
  state.poiTimezone = null;
  els.coverageCard.hidden = true;
  els.coverageCard.innerHTML = "";
  els.poiStopsNote.hidden = true;
  els.poiStopsNote.innerHTML = "";
  els.weatherCard.hidden = true;
  els.weatherCard.innerHTML = "";
  state.profile = null;
  clearProfileCursor();
  els.profileCard.hidden = true;
  state.cueRows = [];
  state.cueLayer.clearLayers();
  els.cuesCard.hidden = true;
  els.traceCard.hidden = true;
  els.traceBody.innerHTML = "";
  state.provisionalLayer.clearLayers();
  state.colourLayer.clearLayers();
  els.coloursCard.hidden = true;
  hideReshapeHandle();
  els.alternatives.hidden = true;
  els.alternativesList.innerHTML = "";
  els.viewingNote.hidden = true;
  els.resultsPanel.hidden = true;
  els.errorsPanel.hidden = true;
  els.clarificationPanel.hidden = true;
  els.clarificationList.innerHTML = "";
  els.optimizationPanel.hidden = true;
  els.optimizationReport.innerHTML = "";
}

/** "Where the time went": the steps of the plan that was just made (a plan from the short-lived
 * cache says so instead; a chat plan carries no trace). */
function renderTrace(data) {
  const html = BikeStream.breakdownHtml(data.trace, data.served_from_cache);
  els.traceCard.hidden = html === "";
  els.traceBody.innerHTML = html;
  els.traceSummary.textContent = data.trace
    ? BikeStream.summaryText(data.trace)
    : data.served_from_cache
      ? "from the short-lived cache"
      : "";
}

function renderResponse(data, secs) {
  state.formRouteUnsent = true; // a route the chat has not been told about (drawChatPlan clears it)
  clearResults();
  renderErrors(data.errors);
  renderCoverage(data);
  renderTrace(data);

  switch (data.status) {
    case "ready":
      setStatus("ok", secs === null ? "Route restored." : `Route ready in ${secs}s.`);
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
  if (data.status !== "ready") schedulePoiRefresh();
}

function getCapabilities() {
  if (!state.capsPromise) {
    state.capsPromise = fetch(`${API_BASE}/v1/capabilities`)
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}));
  }
  return state.capsPromise;
}

/** A routing engine without map data for the trip: put the choice to the user (coverage.js). */
async function renderCoverage(data) {
  const gap = BikeCoverage.find(data.errors);
  if (!gap) {
    els.coverageCard.hidden = true;
    els.coverageCard.innerHTML = "";
    return;
  }
  const hasRoute = data.status === "ready";
  const caps = await getCapabilities();
  const ctx = { downloads: Boolean(caps.segment_downloads), engines: caps.engines || [], info: {} };
  const missing = BikeCoverage.details(gap).missing;
  if (ctx.downloads && missing.length) {
    try {
      const resp = await fetch(`${API_BASE}/v1/routing/segments?names=${encodeURIComponent(missing.join(","))}`);
      if (resp.ok) for (const s of (await resp.json()).segments) ctx.info[s.name] = s;
    } catch {
      // sizes are a courtesy: the button works without them
    }
  }
  // A newer plan may have replaced this one while the sizes were fetched.
  if (state.lastResponse !== data) return;
  const html = BikeCoverage.cardHtml(gap, hasRoute, ctx);
  els.coverageCard.hidden = html === "";
  els.coverageCard.innerHTML = html;
  if (html) revealOnPhone(els.coverageCard);
}

/** Download the tiles the user chose, show progress, and plan the trip again when done. */
async function downloadSegments(segments, button) {
  const box = () => document.getElementById("coverage-progress");
  button.disabled = true;
  const show = (html) => {
    if (box()) box().innerHTML = html;
  };
  try {
    const resp = await fetch(`${API_BASE}/v1/routing/segments/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments }),
    });
    if (!resp.ok) {
      const detail = await safeErrorText(resp);
      show(`<div class="coverage-row failed">${escapeHtml(resp.status === 409 ? "A map download is already running \u2014 wait for it to finish." : detail)}</div>`);
      button.disabled = false;
      return;
    }
    let job = await resp.json();
    state.segmentJob = job.id;
    while (job.state === "running") {
      show(BikeCoverage.progressHtml(job));
      await new Promise((r) => setTimeout(r, 1500));
      if (state.segmentJob !== job.id) return; // reset or another download took over
      const poll = await fetch(`${API_BASE}/v1/routing/segments/download/${job.id}`);
      if (!poll.ok) throw new Error(`HTTP ${poll.status}`);
      job = await poll.json();
    }
    show(BikeCoverage.progressHtml(job));
    if (job.state === "done") {
      setStatus("info", "Map data downloaded &mdash; planning again&hellip;");
      planRoute();
    } else {
      button.disabled = false;
    }
  } catch (err) {
    show(`<div class="coverage-row failed">${escapeHtml(String(err))}</div>`);
    button.disabled = false;
  }
}

/* ------------------------------ shareable link ----------------------------- */

/** Copy a link that opens the planner with the form's trip in it (the trip is in the #fragment,
 * which no server ever sees). Without clipboard access the link is shown to be copied by hand. */
async function copyPlanLink() {
  const keep = state.engineOverride; // buildRequest consumes it; copying a link is not a plan
  const request = buildRequest();
  state.engineOverride = keep;
  const fragment = BikeLink.encode(request);
  if (!fragment) {
    setStatus("error", "Enter an origin and a destination first (and keep the trip small enough for a link).");
    return;
  }
  const url = window.location.href.split("#")[0] + fragment;
  if (await copyText(url)) {
    setStatus("info", "Link copied. It opens this trip in the form; the places are in the link itself, not on a server.");
  } else {
    setStatus("info", `Copy this link: <input class="link-box" type="text" readonly value="${escapeHtml(url)}">`);
    const box = els.status.querySelector(".link-box");
    if (box) box.select();
  }
}

/** A link with a trip in it: fill the form and plan it, before anything else can draw. */
function loadSharedPlan() {
  const request = BikeLink.decode(window.location.hash);
  if (!request) return;
  state.memoryHoldBack = true; // somebody else's trip must not rewrite what this browser remembers
  if (request.max_alternatives) els.maxAlternatives.value = String(request.max_alternatives);
  applyInterpretation({ request, departure_time: request.departure_time });
  planRoute();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // No clipboard access (an http page, a blocked permission): select the text instead.
    return false;
  }
}

/* -------------------------------- GPX import -------------------------------- */

/** The start, the finish and the file's waypoints, on top of the imported track. */
function drawImportMarks(response) {
  state.importLayer.clearLayers();
  const line = response.route.geometry_geojson.coordinates;
  const mark = (lon, lat, label, fill, radius) =>
    L.circleMarker([lat, lon], { radius, color: "#ffffff", weight: 2, fillColor: fill, fillOpacity: 1 })
      .bindTooltip(escapeHtml(label))
      .addTo(state.importLayer);
  const first = line[0];
  const last = line[line.length - 1];
  mark(first[0], first[1], response.imported.loop ? "Start and finish" : "Start", "#0e7a4a", 7);
  if (!response.imported.loop) mark(last[0], last[1], "Finish", "#b91c1c", 7);
  for (const w of response.imported.waypoints) {
    mark(w.lon, w.lat, [w.name, w.description].filter(Boolean).join(" \u2014 ") || "Waypoint", "#475569", 4);
  }
}

/** Show the track of a GPX file like a planned route. The file is read here and goes nowhere. */
async function importGpx(file) {
  const request = ++state.gpxImportRequest; // reading is async: only the latest file may be shown
  const refused = BikeGpxImport.refusal(file);
  if (refused) {
    setStatus("error", escapeHtml(refused));
    return;
  }
  let response;
  try {
    response = BikeGpxImport.toResponse(BikeGpxImport.parse(await file.text()), file.name);
  } catch (err) {
    if (request !== state.gpxImportRequest) return;
    setStatus("error", escapeHtml(BikeGpxImport.errorText(err)));
    return;
  }
  if (request !== state.gpxImportRequest) return;
  if (state.aborted) state.aborted.abort(); // a plan still being made must not replace the file
  state.planGeneration += 1;
  state.renderedGeneration = state.planGeneration;
  state.deferredChat = null;
  state.lastResponse = null; // the planner and the chat know nothing of this route
  renderResponse(response, null);
  state.formRouteUnsent = false; // so the chat is not handed a route it never planned
  drawImportMarks(response);
  setStatus("ok", `Imported ${escapeHtml(response.route.provider_profile)} &mdash; ${escapeHtml(fmtKm(response.route.metrics.distance_m))}. It is drawn as it is in the file.`);
}

function wireImport() {
  els.importBtn.addEventListener("click", () => els.importInput.click());
  els.importInput.addEventListener("change", () => {
    const file = els.importInput.files && els.importInput.files[0];
    els.importInput.value = ""; // choosing the same file again must import it again
    if (file) importGpx(file);
  });
  const holdsFile = (ev) => ev.dataTransfer && Array.from(ev.dataTransfer.types || []).includes("Files");
  const map = state.map.getContainer();
  map.addEventListener("dragover", (ev) => {
    if (!holdsFile(ev)) return;
    ev.preventDefault();
    map.classList.add("file-over");
  });
  map.addEventListener("dragleave", () => map.classList.remove("file-over"));
  map.addEventListener("drop", (ev) => {
    if (!holdsFile(ev)) return;
    ev.preventDefault();
    map.classList.remove("file-over");
    const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (file) importGpx(file);
  });
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
    layer: attachReshape(L.geoJSON(candidate.geometry_geojson, { style: candidateStyle(i) }).addTo(state.map), i),
    color: colorForIndex(i, selectedIndex),
    selected: i === selectedIndex,
  }));
  state.candidateLayers[selectedIndex].layer.bringToFront();

  const allBounds = boundsOfAllCandidates();
  if (allBounds) state.map.fitBounds(allBounds.pad(0.12));

  redrawPlaceMarkers();

  els.resultsPanel.hidden = false;
  els.reshapeHint.hidden = !activeRouteLine() || !("ontouchstart" in window || navigator.maxTouchPoints > 0);
  // One scroll target: when a routing engine lacked map data the card that asks the user what
  // to do is revealed instead (renderCoverage), not the result.
  if (!BikeCoverage.find(data.errors)) revealOnPhone(els.resultsPanel);
  els.explanation.textContent = data.explanation || "";

  renderCandidatesTable();
  renderAlternatives();
  updateActiveView();
  renderArtifacts(data.artifacts);
  renderPoiStops(data);

  const provBits = [];
  if (route.provider) provBits.push(`provider: ${route.provider}`);
  const prov = route.provenance || {};
  for (const key of ["requested_at", "profile", "profile_overrides", "profile_mapping"]) {
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
  if (index !== state.activeIndex) hideReshapeHandle(); // a handle belongs to the route it was put on
  state.activeIndex = index;
  state.candidateLayers.forEach((entry, i) => entry.layer.setStyle(candidateStyle(i)));
  state.candidateLayers[index].layer.bringToFront();
  els.candidatesTable.querySelectorAll("tr.cand-row").forEach((tr, i) => {
    tr.classList.toggle("active", i === index);
  });
  els.alternativesList.querySelectorAll("button[data-candidate]").forEach((btn) => {
    const isActive = Number(btn.dataset.candidate) === index;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
  renderAlternativeDownload();
  updateActiveView();
}

/** Alternative routes as short pros/cons cards; hidden unless two distinct routes came back. */
function renderAlternatives() {
  const selectedIndex = findCandidateIndex(state.candidates, state.selectedRoute);
  const html = BikeAlternatives.alternativesHtml(
    state.candidates,
    state.candidateLayers.map((entry) => entry.color),
    selectedIndex,
    state.activeIndex,
  );
  els.alternatives.hidden = html === "";
  els.alternativesList.innerHTML = html;
  renderAlternativeDownload();
}

/** "Download the route you are viewing" row, for whichever alternative is active. */
function renderAlternativeDownload() {
  const active = state.candidates[state.activeIndex];
  els.altDownload.hidden = !active || els.alternatives.hidden;
  if (!active) return;
  els.altDownloadLabel.textContent = `Download ${BikeAlternatives.styleLabel(active)}:`;
}

function saveFile(text, type, filename) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadActiveCandidate(format) {
  const active = state.candidates[state.activeIndex];
  if (!active) return;
  try {
    const file = BikeExport.build(active, format, `pyBikeRouter ${BikeAlternatives.styleLabel(active)}`);
    saveFile(file.text, file.type, file.filename);
  } catch (err) {
    setStatus("error", `Could not export this route: ${escapeHtml(String(err.message || err))}`);
    return;
  }
  reportPick();
}

/**
 * Tell the server which of the compared routes was taken, if its owner switched the pick log on
 * (capabilities.pick_log). Numbers only (picks.js); never delays or fails the download.
 */
async function reportPick() {
  try {
    if (!(await getCapabilities()).pick_log) return;
    const body = BikePicks.payload(
      state.candidates,
      state.activeIndex,
      findCandidateIndex(state.candidates, state.selectedRoute),
      els.bikeType.value,
      state.lastResponse && state.lastResponse.optimization && state.lastResponse.optimization.goals,
    );
    if (!body || state.pickKeys.has(BikePicks.key(body))) return;
    state.pickKeys.add(BikePicks.key(body));
    await fetch(`${API_BASE}/v1/route/pick`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    });
  } catch {
    /* the pick log is a convenience of its owner; a failure is not the rider's problem */
  }
}

/** A share (0..1) as "19 %", or a dash with the reason when the engine gave none. */
function shareCell(share) {
  return share == null
    ? `<span title="the engine gave no data">&mdash;</span>`
    : BikeFacts.pct(share);
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
    "<th class='num'>Ascent</th>" +
    "<th class='num' title='Share on busy roads: an estimate from the mapped road class, cycle lanes and speed limits, not a count of cars'>Busy roads</th>" +
    "<th class='num' title='Share on paved surfaces, from the OSM tags the engine routed over'>Paved</th>" +
    "<th class='num' title='Average wind against (&#9650;) or with (&#9660;) the direction of travel, km/h'>Wind</th>" +
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
        `<td class="num">${shareCell(BikeFacts.busyShare(m))}</td>` +
        `<td class="num">${shareCell(BikeFacts.pavedShare(m))}</td>` +
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

/* ---------------------------- route colours (map) ---------------------------- */

const COLOUR_KEY = "pybikerouter.colour";

function loadColourMode() {
  try {
    const saved = window.localStorage.getItem(COLOUR_KEY);
    return ["surface", "traffic", "gradient"].includes(saved) ? saved : "none";
  } catch {
    return "none"; // storage blocked: just not remembered
  }
}

function setColourMode(mode) {
  state.colourMode = mode;
  try {
    window.localStorage.setItem(COLOUR_KEY, mode);
  } catch {
    /* not remembered */
  }
  const entry = state.candidateLayers[state.activeIndex];
  if (entry) renderColours(entry.candidate);
}

const GRADIENT_NOTE =
  "The grade over about 300 m of the route, from the smoothed elevation; the same colours as the chart below.";

/** The active route cut into surface, traffic or gradient colours, with what each covers (hidden
 * when the route has no tags and no elevation). */
function renderColours(cand) {
  state.colourLayer.clearLayers();
  const profile = BikeProfile.build(cand.geometry_geojson);
  const modes = [...BikeStretches.modes(cand), ...(profile ? ["gradient"] : [])];
  if (!modes.length) {
    els.coloursCard.hidden = true;
    return;
  }
  els.coloursCard.hidden = false;
  const mode = modes.includes(state.colourMode) ? state.colourMode : "none";
  const choices = [["none", "Plain"], ...modes.map((m) => [m, m === "gradient" ? "Gradient" : BikeStretches.MODES[m].label])];
  els.coloursModes.innerHTML = choices
    .map(
      ([key, label]) =>
        `<button type="button" class="ghost colour-mode${key === mode ? " active" : ""}" data-colour="${key}" aria-pressed="${key === mode}">${escapeHtml(label)}</button>`,
    )
    .join("");
  const shown = mode !== "none";
  els.coloursLegend.hidden = !shown;
  els.coloursLongest.hidden = !shown;
  els.coloursNote.hidden = !shown;
  if (!shown) return;
  const gradient = mode === "gradient";
  const pieces = gradient ? BikeProfile.bandPieces(profile) : BikeStretches.pieces(cand, mode);
  for (const piece of pieces) {
    L.polyline(piece.latlngs, { color: piece.color, weight: 6, opacity: 0.95, interactive: false }).addTo(state.colourLayer);
  }
  els.coloursLegend.innerHTML = BikeStretches.legendHtml(
    gradient ? BikeProfile.bandLegend(pieces) : BikeStretches.legend(cand, mode),
  );
  els.coloursLongest.textContent = gradient ? "" : BikeStretches.longestText(BikeStretches.longest(cand, mode));
  els.coloursNote.textContent = gradient ? GRADIENT_NOTE : BikeStretches.MODES[mode].note;
}

/* -------------------------------- directions -------------------------------- */

/** The turn-by-turn list of the route shown (hidden when the engine gave no cues). */
function renderCues(cand) {
  state.cueLayer.clearLayers();
  const ride = cand.weather ? { departure: cand.weather.departure, arrival: cand.weather.arrival } : null;
  const rows = BikeCues.steps(cand.cues, cand.metrics && cand.metrics.distance_m, ride);
  state.cueRows = rows;
  if (!rows.length) {
    els.cuesCard.hidden = true;
    return;
  }
  els.cuesCard.hidden = false;
  els.cuesSummary.textContent = BikeCues.summary(rows);
  els.cuesList.innerHTML = BikeCues.listHtml(rows);
}

/** Show where a step of the list is on the map. */
function showCue(index) {
  const row = state.cueRows[index];
  const entry = state.candidateLayers[state.activeIndex];
  if (!row || !entry) return;
  state.cueLayer.clearLayers();
  let at = null;
  if (row.cue) at = [row.cue.lat, row.cue.lon];
  else {
    const coords = entry.candidate.geometry_geojson.coordinates || [];
    const p = row.distanceM === 0 ? coords[0] : coords[coords.length - 1];
    if (p) at = [p[1], p[0]];
  }
  if (!at) return;
  L.circleMarker(at, { radius: 9, weight: 3, color: "#c43d32", fillColor: "#ffffff", fillOpacity: 1, interactive: false }).addTo(
    state.cueLayer,
  );
  state.map.panTo(at);
}

function downloadCueSheet() {
  const active = state.candidates[state.activeIndex];
  if (!active || !state.cueRows.length) return;
  const title = `pyBikeRouter ${BikeAlternatives.styleLabel(active)}`;
  saveFile(BikeCues.sheetText(state.cueRows, title), "text/plain;charset=utf-8", BikeExport.filename(active, "txt").replace(/^route-/, "cues-"));
}

/* ----------------------------- elevation profile ----------------------------- */

const PROFILE_HOVER_HINT = "Move over the chart to see the grade and where it is on the map.";

function clearProfileCursor() {
  state.profileLayer.clearLayers();
  state.profileMarker = null;
  const cursor = els.profileChart.querySelector(".profile-cursor");
  if (cursor) cursor.setAttribute("hidden", "");
  els.profileHover.textContent = PROFILE_HOVER_HINT;
}

/** Put the chart's cursor and the marker on the map at `distanceM` along the route. */
function showProfileCursor(distanceM) {
  const profile = state.profile;
  const svg = els.profileChart.querySelector("svg");
  if (!profile || !svg) return;
  const here = BikeProfile.at(profile, distanceM);
  const left = Number(svg.dataset.left);
  const right = Number(svg.dataset.right);
  const x = left + (here.distanceM / profile.totalM) * (right - left);
  const cursor = svg.querySelector(".profile-cursor");
  cursor.removeAttribute("hidden");
  cursor.setAttribute("transform", `translate(${x.toFixed(1)} 0)`);
  const percent = here.grade * 100;
  const sign = percent > 0.5 ? "+" : percent < -0.5 ? "−" : "";
  els.profileHover.textContent =
    `km ${(here.distanceM / 1000).toFixed(1)} · ${Math.round(here.elevationM)} m · ` +
    `${sign}${Math.abs(percent).toFixed(1)} %`;
  if (!state.profileMarker) {
    state.profileMarker = L.circleMarker([here.lat, here.lon], {
      radius: 8, weight: 3, color: "#c43d32", fillColor: "#ffffff", fillOpacity: 1, interactive: false,
    }).addTo(state.profileLayer);
  } else {
    state.profileMarker.setLatLng([here.lat, here.lon]);
  }
}

function renderProfile(cand) {
  const profile = BikeProfile.build(cand.geometry_geojson);
  state.profile = profile;
  clearProfileCursor();
  if (!profile) {
    els.profileCard.hidden = true; // no elevation in this route (or too short): nothing to draw
    return;
  }
  els.profileCard.hidden = false;
  els.profileChart.innerHTML = BikeProfile.chartSvg(profile);
  const svg = els.profileChart.querySelector("svg");
  svg.setAttribute("tabindex", "0");
  const fractionAt = (ev) => {
    const rect = svg.getBoundingClientRect();
    const width = Number(svg.dataset.width);
    const left = Number(svg.dataset.left);
    const right = Number(svg.dataset.right);
    return ((((ev.clientX - rect.left) / rect.width) * width) - left) / (right - left);
  };
  const onPointer = (ev) => showProfileCursor(BikeProfile.distanceAtFraction(profile, fractionAt(ev)));
  svg.addEventListener("pointermove", onPointer);
  svg.addEventListener("pointerdown", onPointer);
  svg.addEventListener("pointerleave", clearProfileCursor);
  svg.addEventListener("pointercancel", clearProfileCursor);
  svg.addEventListener("blur", clearProfileCursor);
  // Keyboard: arrows move the cursor by 2 % of the route, Home and End jump to the ends.
  let keyboardAt = 0;
  svg.addEventListener("keydown", (ev) => {
    const step = profile.totalM * 0.02;
    if (ev.key === "ArrowRight") keyboardAt += step;
    else if (ev.key === "ArrowLeft") keyboardAt -= step;
    else if (ev.key === "Home") keyboardAt = 0;
    else if (ev.key === "End") keyboardAt = profile.totalM;
    else return;
    ev.preventDefault();
    keyboardAt = Math.max(0, Math.min(profile.totalM, keyboardAt));
    showProfileCursor(keyboardAt);
  });

  const facts = [];
  facts.push(`Highest ${Math.round(profile.max)} m, lowest ${Math.round(profile.min)} m`);
  const steep = BikeProfile.steepest(profile);
  if (Math.abs(steep.grade) >= 0.06) {
    facts.push(
      `Steepest 100 m: ${Math.abs(steep.grade * 100).toFixed(0)} % ${steep.grade > 0 ? "up" : "down"}, ` +
        `at km ${(steep.atM / 1000).toFixed(1)}`,
    );
  }
  for (const k of BikeProfile.climbs(profile, 3)) {
    facts.push(
      `Climb at km ${(k.fromM / 1000).toFixed(1)}: ${(k.lengthM / 1000).toFixed(1)} km, ` +
        `+${Math.round(k.gainM)} m, ${(k.grade * 100).toFixed(1)} % on average`,
    );
  }
  els.profileFacts.innerHTML = facts.map((f) => `<li>${escapeHtml(f)}</li>`).join("");
  els.profileLegend.innerHTML = BikeProfile.GRADE_BANDS.map(
    (b) => `<li><span class="profile-swatch" style="background:${b.color}"></span>${escapeHtml(b.label)}</li>`,
  ).join("");
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
    metric(
      BikeFacts.busyShare(m) == null ? "&mdash;" : BikeFacts.pct(BikeFacts.busyShare(m)),
      "Busy roads"
    ) +
    metric(
      BikeFacts.pavedShare(m) == null ? "&mdash;" : BikeFacts.pct(BikeFacts.pavedShare(m)),
      "Paved"
    ) +
    metric(cand.score != null ? cand.score.toFixed(2) : "—", "Score") +
    metric(escapeHtml(cand.provider_profile || "—"), "Profile");

  renderColours(cand);
  renderProfile(cand);
  renderCues(cand);
  renderWeather(cand);
  schedulePoiRefresh();
  renderScoreBreakdown(cand.score_breakdown);
  const shares = BikeFacts.surfaceShares(m);
  renderSurfaces(
    Object.fromEntries(shares),
    shares.some(([k]) => k === "unknown") ? null : m.unknown_surface_fraction
  );

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
  if (originCoord) points.push({ ...originCoord, label: "A", cls: "", input: els.origin });
  viaInputs().forEach((input, idx) => {
    const c = parseInputCoordinate(input.value);
    if (c) points.push({ ...c, label: String(idx + 1), cls: "", input });
  });
  if (destCoord) points.push({ ...destCoord, label: "B", cls: "dest", input: els.destination });

  for (const p of points) {
    const icon = L.divIcon({
      className: "place-marker",
      html: `<span class="place-badge ${p.cls}">${p.label}</span>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    const marker = L.marker([p.lat, p.lon], { icon, draggable: true, title: "Drag to move this place" }).addTo(state.placeMarkers);
    marker.on("dragend", () => {
      guardClickAfterDrag();
      movePlace(p.input, marker.getLatLng());
    });
  }
}

/** A place marker was dropped somewhere else: that place becomes the dropped point, and the trip is
 * planned again (the same as typing the coordinates in its field and pressing Plan route). */
function movePlace(input, latlng) {
  const ll = latlng.wrap();
  input.value = BikeWaypoints.coordText(ll.lat, ll.lng);
  markCoordInput(input);
  if (els.origin.value.trim() && els.destination.value.trim()) planRoute(); // redraws the markers
  else redrawPlaceMarkers();
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
  els.coverageCard.addEventListener("click", async (ev) => {
    const action = ev.target.closest("button[data-action]");
    if (action) {
      if (action.dataset.action === "download") {
        downloadSegments(action.dataset.segments.split(","), action);
      } else if (action.dataset.action === "use-engine") {
        state.engineOverride = [action.dataset.engine];
        planRoute();
      }
      return;
    }
    const button = ev.target.closest("button[data-copy]");
    if (!button) return;
    const source = $(button.dataset.copy);
    if (!source) return;
    if (await copyText(source.textContent)) {
      button.textContent = "Copied";
    } else {
      const range = document.createRange();
      range.selectNodeContents(source);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = "Selected \u2014 press Ctrl+C";
    }
  });
  els.returnOrigin.addEventListener("change", updatePoiStopsAvailability);
  els.poiStopsEnabled.addEventListener("change", updatePoiStopsAvailability);

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
  els.altDownload.addEventListener("click", (ev) => {
    const button = ev.target.closest("button[data-export]");
    if (button) downloadActiveCandidate(button.dataset.export);
  });
  els.alternativesList.addEventListener("click", (ev) => {
    const card = ev.target.closest("button[data-candidate]");
    if (card) activateCandidate(Number(card.dataset.candidate));
  });
  // "Other departure times": choosing one sets the departure picker and plans again.
  els.weatherCard.addEventListener("click", (ev) => {
    const row = ev.target.closest("button[data-departure]");
    if (!row) return;
    const local = BikeText.toLocalInputValue(row.dataset.departure);
    if (!local) return;
    els.departurePreset.value = "custom";
    els.departureCustom.value = local;
    updateDepartureHint();
    planRoute();
  });
  els.textBtn.addEventListener("click", planFromText);
  els.textInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      planFromText();
    }
  });
  els.resetBtn.addEventListener("click", resetAll);
  els.copyLinkBtn.addEventListener("click", copyPlanLink);
  wireImport();
  els.cuesList.addEventListener("click", (ev) => {
    const button = ev.target.closest("button[data-cue]");
    if (button) showCue(Number(button.dataset.cue));
  });
  els.cuesDownload.addEventListener("click", downloadCueSheet);
  els.coloursModes.addEventListener("click", (ev) => {
    const button = ev.target.closest("button[data-colour]");
    if (button) setColourMode(button.dataset.colour);
  });

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
  els.optimizeDetours.checked = false;
  els.returnOrigin.checked = false;
  els.poiStopsEnabled.checked = false;
  updatePoiStopsAvailability();
  els.textInput.value = "";
  for (const id of ["origin-input", "destination-input"]) markCoordInput($(id));
  clearResults();
  setStatus("info", "Reset. Enter two places and press <strong>Plan route</strong>.") ;
  setTimeout(() => setStatus(null, ""), 2500);
  state.map.setView([51.75, -1.25], 12);
  schedulePoiRefresh();
}

/* ------------------------- points of interest ---------------------------- */

const POI_MIN_ZOOM = 11; // below this a map view is too large to list POIs for
const POI_ROUTE_POINTS = 1500; // route shape sent to the server is thinned to this

/** Show the POI controls only when the server has POIs, and build the kind filter. */
async function initPois() {
  try {
    const caps = await (await fetch(`${API_BASE}/v1/capabilities`)).json();
    if (!caps.pois) return;
    const resp = await fetch(`${API_BASE}/v1/pois/categories`);
    if (!resp.ok) return;
    state.poiCategories = await resp.json();
  } catch {
    return; // older server or offline: the form works exactly as before
  }
  state.poiPrefs = BikePois.loadPrefs(window.localStorage, state.poiCategories);
  els.poiEnabled.checked = state.poiPrefs.enabled;
  els.poiCategories.innerHTML = state.poiCategories
    .map(
      (c) =>
        `<label class="check"><input type="checkbox" data-key="${escapeHtml(c.key)}"${
          state.poiPrefs.keys.includes(c.key) ? " checked" : ""
        } /> ${BikePois.icon(c.key)} ${escapeHtml(c.label)}</label>`,
    )
    .join("");
  const onChange = () => {
    state.poiPrefs = {
      enabled: els.poiEnabled.checked,
      keys: [...els.poiCategories.querySelectorAll("input:checked")].map((i) => i.dataset.key),
    };
    BikePois.savePrefs(window.localStorage, state.poiPrefs);
    schedulePoiRefresh();
  };
  els.poiEnabled.addEventListener("change", onChange);
  els.poiCategories.addEventListener("change", onChange);
  els.poiPanel.hidden = false;
  els.poiSettings.hidden = false;
  updateSettingsEmpty();
  updatePoiStopsAvailability();
  schedulePoiRefresh();
}

function updatePoiStopsAvailability() {
  const loop = els.returnOrigin.checked;
  els.poiStopsField.hidden = !els.poiStopsEnabled.checked;
  els.poiStopsHint.textContent = loop
    ? "Looks for the best-known sights close to the loop (within 2 km) and routes past them; the loop is drawn a little smaller to make room for the detours. Uses the sight kinds ticked above."
    : "Picks the best-known sights between origin and destination (by how many languages describe them) and adds them as stops. Uses the sight kinds ticked above.";
}

/** The `poi_stops` part of a plan request, or null when not asked for. */
function poiStopsRequest() {
  if (!state.poiCategories.length || !els.poiStopsEnabled.checked) return null;
  const sights = state.poiCategories.filter((c) => c.kind === "sight").map((c) => c.key);
  const chosen = state.poiPrefs.keys.filter((k) => sights.includes(k));
  return { count: Number(els.poiStopsCount.value), categories: chosen.length ? chosen : sights };
}

function schedulePoiRefresh() {
  clearTimeout(state.poiTimer);
  state.poiTimer = setTimeout(refreshPois, 350);
}

function setPoiStatus(text) {
  els.poiStatus.textContent = text;
}

async function refreshPois() {
  if (!state.poiCategories.length) return;
  const request = ++state.poiRequest;
  state.poiLayer.clearLayers();
  if (!state.poiPrefs.enabled || !state.poiPrefs.keys.length) {
    setPoiStatus(state.poiPrefs.enabled ? "Tick at least one kind to see it on the map." : "");
    return;
  }
  const entry = state.candidateLayers[state.activeIndex];
  let resp;
  try {
    if (entry) {
      const coords = BikePois.thin(entry.candidate.geometry_geojson.coordinates, POI_ROUTE_POINTS);
      setPoiStatus("Looking for points of interest along the route\u2026");
      resp = await fetch(`${API_BASE}/v1/pois/along-route`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ coordinates: coords, categories: state.poiPrefs.keys }),
      });
    } else {
      const b = state.map.getBounds();
      if (state.map.getZoom() < POI_MIN_ZOOM || BikePois.viewTooLarge(b.getWest(), b.getSouth(), b.getEast(), b.getNorth())) {
        // A wide window shows more than the server will list at zoom 11 and 12 alike.
        setPoiStatus("Zoom in to see points of interest in the map view.");
        return;
      }
      setPoiStatus("Looking for points of interest in the map view\u2026");
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",");
      resp = await fetch(
        `${API_BASE}/v1/pois/in-bbox?bbox=${bbox}&categories=${encodeURIComponent(state.poiPrefs.keys.join(","))}`,
      );
    }
    if (request !== state.poiRequest) return;
    if (!resp.ok) {
      setPoiStatus(
        resp.status === 422 && !entry
          ? "Zoom in to see points of interest in the map view."
          : resp.status === 502
          ? "The map data service (Overpass) is busy right now; points of interest could not be loaded."
          : "Points of interest could not be loaded.",
      );
      return;
    }
    const data = await resp.json();
    if (request !== state.poiRequest) return;
    state.poiTimezone = (entry && data.timezone) || null;
    drawPois(data);
  } catch {
    if (request === state.poiRequest) setPoiStatus("Points of interest could not be loaded.");
  }
}

function drawPois(data) {
  for (const poi of data.pois) addPoiMarker(poi, state.poiLayer, false);
  const where = state.candidateLayers.length ? "along the route" : "in the view";
  const ranked = data.fame_status === "unavailable" ? " (the fame lookup was unavailable)" : "";
  setPoiStatus(
    data.pois.length
      ? `${data.pois.length} points of interest ${where}${data.truncated ? ", the best-known shown" : ""}${ranked}.`
      : `No points of interest of these kinds ${where}.`,
  );
}

function addPoiMarker(poi, layer, isStop) {
  const famous = BikePois.isFamous(poi);
  const size = isStop ? 32 : famous ? 30 : poi.kind === "service" ? 20 : 24;
  const cls = ["poi-marker", poi.kind, famous ? "famous" : "", isStop ? "stop" : ""].join(" ");
  const icon = L.divIcon({
    className: "",
    html: `<span class="${cls}" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.6)}px">${BikePois.icon(poi.category)}</span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
  const marker = L.marker([poi.lat, poi.lon], {
    icon,
    zIndexOffset: isStop ? 1000 : famous ? 500 : 0,
    title: poi.name || poi.category,
    keyboard: true,
  }).addTo(layer);
  marker.bindPopup(() => poiPopup(poi), { maxWidth: 280 });
}

/** What the opening-hours check needs: when the active route is ridden, how long it is, where. */
function rideForHours() {
  const entry = state.candidateLayers[state.activeIndex];
  const cand = entry && entry.candidate;
  if (!cand || !cand.weather || !state.poiTimezone) return null;
  return {
    departure: cand.weather.departure,
    arrival: cand.weather.arrival,
    distance_m: cand.metrics && cand.metrics.distance_m,
    timezone: state.poiTimezone,
  };
}

function poiPopup(poi) {
  const el = document.createElement("div");
  el.innerHTML = BikePois.popupHtml(poi, state.poiCategories, BikeHours.check(poi, rideForHours()));
  const infoBox = el.querySelector(".poi-info");
  const infoBtn = el.querySelector('[data-act="info"]');
  if (infoBtn) {
    infoBtn.addEventListener("click", async () => {
      infoBtn.disabled = true;
      infoBox.textContent = "Loading\u2026";
      try {
        const lang = (navigator.language || "en").toLowerCase();
        const resp = await fetch(`${API_BASE}/v1/pois/info?${BikePois.infoQuery(poi, lang)}`);
        infoBox.innerHTML = resp.ok ? BikePois.infoHtml(await resp.json()) : "No description available right now.";
        infoBtn.remove();
      } catch {
        infoBox.textContent = "No description available right now.";
        infoBtn.disabled = false;
      }
    });
  }
  el.querySelector('[data-act="add"]').addEventListener("click", () => {
    state.map.closePopup();
    addPoiToRoute(poi);
  });
  return el;
}

/** Put a POI into the form: an empty origin / destination first, else a via point in travel order. */
function addPoiToRoute(poi) {
  const value = `${poi.lat.toFixed(5)}, ${poi.lon.toFixed(5)}`;
  const label = poi.name || poi.category;
  if (els.origin.value.trim() === "") {
    els.origin.value = value;
    markCoordInput(els.origin);
    els.origin.title = label;
  } else if (els.destination.value.trim() === "" && !els.returnOrigin.checked) {
    els.destination.value = value;
    markCoordInput(els.destination);
    els.destination.title = label;
  } else {
    if (viaInputs().length >= BikePois.MAX_VIAS) {
      setStatus("error", `At most ${BikePois.MAX_VIAS} via points; remove one first.`);
      return;
    }
    const entry = state.candidateLayers[state.activeIndex];
    const routeCoords = entry ? entry.candidate.geometry_geojson.coordinates : null;
    const vias = viaInputs().map((input) => parseInputCoordinate(input.value));
    const index = BikePois.insertionIndex(routeCoords, vias, poi);
    addViaRow(value);
    const rows = els.viaList.children;
    const row = rows[rows.length - 1];
    row.querySelector("input").title = label;
    els.viaList.insertBefore(row, rows[index]);
  }
  redrawPlaceMarkers();
  if (state.candidateLayers.length && els.origin.value.trim() && els.destination.value.trim()) {
    setStatus("info", `Added <strong>${escapeHtml(label)}</strong> to the route &mdash; planning again&hellip;`);
    planRoute();
  } else {
    setStatus("info", `Added <strong>${escapeHtml(label)}</strong>. Press <strong>Plan route</strong> when ready.`);
  }
}

/** Mark the famous sights the plan was routed past and say what happened. */
function renderPoiStops(data) {
  const note = BikePois.stopsNote(data.poi_stops_status, data.poi_stops);
  els.poiStopsNote.hidden = note === "";
  els.poiStopsNote.innerHTML = note;
  for (const poi of data.poi_stops || []) addPoiMarker(poi, state.poiStopLayer, true);
}

/* --------------------------------- chat --------------------------------- */

function chatSay(role, text, pending = false) {
  const wrapper = document.createElement("div");
  wrapper.innerHTML = BikeChat.bubbleHtml(role, text);
  const node = wrapper.firstElementChild;
  if (pending) node.classList.add("pending");
  els.chatLog.appendChild(node);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  return node;
}

function setChatChips(suggestions) {
  els.chatChips.innerHTML = BikeChat.chipsHtml(suggestions);
}

/** Send one chat message; draw the reply, the quick answers and the plan, if there is one. */
async function sendChat(text) {
  const message = BikeChat.outgoing(text);
  if (message === null || state.chatBusy) return;
  state.chatBusy = true;
  els.chatSend.disabled = true;
  chatSay("user", message);
  setChatChips([]);
  const waiting = chatSay("bot", "\u2026", true);
  const generation = state.planGeneration;
  const startedAt = performance.now();
  // What the form holds: the chat refines that, not a route it drew before the form was changed.
  const context = BikeChat.formContext({
    request: buildRequest(),
    plan: state.lastResponse,
    routeUnsent: state.formRouteUnsent,
    dirty: state.formDirty,
  });
  try {
    const resp = await fetch(`${API_BASE}/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: state.chatSession,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        context: context || undefined,
      }),
    });
    waiting.remove();
    if (!resp.ok) {
      chatSay("bot", BikeChat.failureText(resp.status));
      return;
    }
    const data = await resp.json();
    state.formRouteUnsent = false; // the chat has been told what the form held
    state.formDirty = false;
    state.chatSession = data.session_id;
    BikeChat.rememberSession(window.localStorage, state.chatSession);
    if (els.chatEarlierBox.open) refreshEarlier(); // the list shows this conversation's latest use
    chatSay("bot", data.reply);
    setChatChips(data.suggestions);
    if (data.plan) {
      const secs = ((performance.now() - startedAt) / 1000).toFixed(1);
      const stale = generation !== state.planGeneration; // the form asked for a route meanwhile
      if (!stale || (state.planPending === null && state.renderedGeneration !== state.planGeneration)) {
        // Current -- or the newer form request failed and left nothing on the map.
        drawChatPlan(data.plan, data.focus_rank, secs, data.request);
      } else {
        chatSay("bot", "(The map shows the route you asked for in the form meanwhile.)");
        // If that form request is still running and then fails, this route is shown after all.
        if (state.planPending !== null) {
          state.deferredChat = { plan: data.plan, focusRank: data.focus_rank, secs, request: data.request };
        }
      }
    }
  } catch {
    waiting.remove();
    chatSay("bot", BikeChat.failureText(0));
  } finally {
    state.chatBusy = false;
    els.chatSend.disabled = false;
    els.chatInput.focus();
  }
}

/** Draw the plan a chat turn produced; it is now the newest plan on the map. The request it was
 * planned from fills the Plan form, so the person can adjust it there and plan again. */
function drawChatPlan(plan, focusRank, secs, request) {
  state.planGeneration += 1;
  state.renderedGeneration = state.planGeneration;
  state.deferredChat = null;
  if (state.aborted) state.aborted.abort(); // an older form request must not replace it
  state.lastResponse = plan;
  renderResponse(plan, secs);
  state.formRouteUnsent = false; // the chat drew this route: it knows it
  const index = BikeChat.candidateIndex(plan, focusRank);
  if (index >= 0) activateCandidate(index);
  if (request) applyInterpretation({ request, departure_time: request.departure_time });
}

const CHAT_GREETING =
  'Hi! Tell me where you want to ride -- for example "from Braunschweig to Goslar by gravel bike" or "40 km loop from Goslar". Or say "plan a route" and I will ask.';

/** The empty conversation: the greeting and the starting suggestions. */
function chatGreeting() {
  els.chatLog.innerHTML = "";
  chatSay("bot", CHAT_GREETING);
  setChatChips(["Plan a route", "40 km loop from Goslar", "Help"]);
}

/**
 * Show a stored conversation: its messages, quick answers and last route.
 * `explicit` (the person opened it) replaces the conversation and the map; otherwise (the
 * page was just reopened) it only fills a conversation and a map that are still empty.
 * Resolves true when it was shown, false when the server does not know it (it is then dropped
 * from this browser's list).
 */
async function loadChat(id, explicit) {
  try {
    const resp = await fetch(`${API_BASE}/v1/chat/${id}`);
    if (resp.status === 404) {
      BikeChat.forgetKnown(window.localStorage, id);
      if (BikeChat.storedSession(window.localStorage) === id) BikeChat.rememberSession(window.localStorage, null);
      return false; // expired or dropped: start afresh
    }
    if (!resp.ok) return false;
    const history = await resp.json();
    const messages = BikeChat.historyMessages(history);
    // Anything typed meanwhile wins when the page was only reopened.
    if (!explicit && (state.chatSession !== null || state.chatBusy || messages.length === 0)) return false;
    if (state.chatBusy) return false;
    state.chatSession = history.session_id;
    BikeChat.rememberSession(window.localStorage, state.chatSession);
    els.chatLog.innerHTML = "";
    for (const m of messages) chatSay(m.role, m.text);
    setChatChips(history.suggestions);
    if (history.plan && (explicit || (!state.lastResponse && state.planPending === null))) {
      drawChatPlan(history.plan, history.focus_rank, null, history.request);
    }
    return true;
  } catch {
    return false; // the server could not be reached: the chat just starts empty
  }
}

/** Bring back the conversation of an earlier visit, when the server still has it. */
async function restoreChat() {
  const id = BikeChat.storedSession(window.localStorage);
  if (id) await loadChat(id, false);
}

/** A fresh conversation. The route on the map stays; the old conversation stays in the list. */
function newChat() {
  if (state.chatBusy) return;
  state.chatSession = null;
  BikeChat.rememberSession(window.localStorage, null);
  chatGreeting();
  if (els.chatEarlierBox.open) refreshEarlier();
  els.chatInput.focus();
}

/** Fill the list of earlier conversations from what the server still knows of this browser's ids. */
async function refreshEarlier() {
  const ids = BikeChat.knownSessions(window.localStorage);
  let sessions = [];
  if (ids.length) {
    try {
      const resp = await fetch(`${API_BASE}/v1/chat/summaries`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_ids: ids }),
      });
      if (!resp.ok) return;
      sessions = (await resp.json()).sessions || [];
    } catch {
      return; // offline: leave the list as it was
    }
    BikeChat.keepKnown(window.localStorage, sessions.map((s) => s.session_id));
  }
  els.chatEarlier.innerHTML = BikeChat.conversationsHtml(sessions, Date.now() / 1000, state.chatSession);
  els.chatEarlierEmpty.hidden = sessions.length > 0;
}

async function openEarlier(id) {
  if (state.chatBusy || id === state.chatSession) return;
  const shown = await loadChat(id, true);
  if (!shown) chatSay("bot", "That conversation is no longer stored.");
  refreshEarlier();
}

async function deleteEarlier(id) {
  if (state.chatBusy) return;
  if (!window.confirm("Delete this conversation? Its messages are removed from the server.")) return;
  try {
    const resp = await fetch(`${API_BASE}/v1/chat/${id}`, { method: "DELETE" });
    if (!resp.ok && resp.status !== 404) return; // not deleted: keep it in the list
  } catch {
    return;
  }
  BikeChat.forgetKnown(window.localStorage, id);
  if (id === state.chatSession) newChat();
  refreshEarlier();
}

/** Show the chat only when the server has one. */
async function initChat() {
  const caps = await getCapabilities();
  if (!caps.chat) return;
  els.chatPanel.hidden = false;
  chatGreeting();
  restoreChat(); // the conversation of an earlier visit, when the server still has it
  els.chatNew.addEventListener("click", newChat);
  els.chatEarlierBox.addEventListener("toggle", () => {
    if (els.chatEarlierBox.open) refreshEarlier();
  });
  els.chatEarlier.addEventListener("click", (ev) => {
    const open = ev.target.closest("[data-open]");
    if (open) return openEarlier(open.dataset.open);
    const del = ev.target.closest("[data-delete]");
    if (del) deleteEarlier(del.dataset.delete);
  });
  els.chatForm.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = els.chatInput.value;
    els.chatInput.value = "";
    sendChat(text);
  });
  els.chatChips.addEventListener("click", (ev) => {
    const chip = ev.target.closest("button[data-say]");
    if (chip) sendChat(chip.dataset.say);
  });
}

/* ------------------------------ service status ------------------------------ */

const STATUS_POLL_MS = 60000;

/** Ask /readyz which services are up and show it: a dot in the header and a list on click.
 * The report comes with 200 and with 503; a server without /readyz (404) hides the dot. */
async function refreshStatus() {
  let report = null;
  let unreachable = false;
  try {
    const resp = await fetch(`${API_BASE}/readyz`);
    if (resp.status === 404) {
      els.serviceStatus.hidden = true;
      return;
    }
    report = await resp.json();
  } catch {
    unreachable = true; // the page was served by this server a moment ago: it is down or we are offline
  }
  const level = unreachable ? "unavailable" : BikeStatus.levelOf(report);
  if (level === null) {
    els.serviceStatus.hidden = true;
    return;
  }
  const text = unreachable ? "Cannot reach the server" : BikeStatus.summaryText(report);
  els.serviceStatus.hidden = false;
  els.serviceStatus.dataset.level = level;
  els.serviceStatus.title = text;
  els.serviceStatusText.textContent = `Service status: ${text}`;
  els.serviceStatusSummary.textContent = text;
  els.serviceStatusList.innerHTML = unreachable ? "" : BikeStatus.rowsHtml(report);
  const checked = report && report.checked_at ? Date.parse(report.checked_at) / 1000 : Date.now() / 1000;
  els.serviceStatusChecked.textContent = `Checked ${BikeChat.relativeTime(checked, Date.now() / 1000)}.`;
}

function initStatus() {
  els.serviceStatus.addEventListener("click", () => {
    const open = els.serviceStatusPanel.hidden;
    els.serviceStatusPanel.hidden = !open;
    els.serviceStatus.setAttribute("aria-expanded", String(open));
    if (open) refreshStatus();
  });
  refreshStatus();
  setInterval(() => {
    if (document.visibilityState === "visible") refreshStatus();
  }, STATUS_POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshStatus();
  });
}

/* ------------------------------ optimiser tab ------------------------------ */

const TAB_KEY = "pybikerouter.tab";

function setTab(name) {
  state.tab = name === "optimize" ? "optimize" : "plan";
  document.body.dataset.tab = state.tab;
  for (const btn of els.modeTabs.querySelectorAll("[data-tab-btn]")) {
    btn.setAttribute("aria-selected", String(btn.dataset.tabBtn === state.tab));
  }
  try {
    window.localStorage.setItem(TAB_KEY, state.tab);
  } catch {
    // private window or blocked storage: the tab just is not remembered
  }
}

function applyGoalState() {
  const picked = Object.keys(state.optimizeGoals);
  for (const def of BikeOptimize.GOALS) {
    const row = els.optimizeGoals.querySelector(`[data-goal="${def.metric}"]`);
    if (!row) continue;
    const on = def.metric in state.optimizeGoals;
    const box = row.querySelector("input[type=checkbox]");
    const level = row.querySelector("select");
    const unavailable = def.needs && !state.optimizeCaps[def.needs];
    box.checked = on;
    level.value = state.optimizeGoals[def.metric] || "medium";
    level.disabled = !on;
    row.classList.toggle("on", on);
    row.classList.toggle("off", Boolean(unavailable));
    // At most four goals, and no goal beside its opposite.
    const full = !on && picked.length >= BikeOptimize.MAX_GOALS;
    box.disabled = Boolean(unavailable) || full;
  }
  els.optimizePoiOptions.hidden = !("pois" in state.optimizeGoals);
}

function renderGoals() {
  els.optimizeGoals.innerHTML = BikeOptimize.GOALS.map((g) => {
    const levels = BikeOptimize.LEVELS.map(
      (l) => `<option value="${l.level}">${BikeOptimize.escapeHtml(l.label)}</option>`,
    ).join("");
    const unavailable = g.needs && !state.optimizeCaps[g.needs];
    const hint = unavailable ? "not available on this server" : g.hint;
    return (
      `<div class="opt-goal" data-goal="${g.metric}">` +
      `<label><input type="checkbox" /><span>${BikeOptimize.escapeHtml(g.label)}` +
      `<span class="goal-hint">${BikeOptimize.escapeHtml(hint)}</span></span></label>` +
      `<select aria-label="How much ${BikeOptimize.escapeHtml(g.label.toLowerCase())} matters">${levels}</select>` +
      "</div>"
    );
  }).join("");
  applyGoalState();
}

function setGoals(goals) {
  state.optimizeGoals = {};
  for (const g of goals) {
    const def = BikeOptimize.GOALS.find((d) => d.metric === g.metric);
    if (!def || (def.needs && !state.optimizeCaps[def.needs])) continue;
    state.optimizeGoals[g.metric] = g.level;
  }
  applyGoalState();
}

function optimizeChoice() {
  return {
    goals: BikeOptimize.GOALS.filter((d) => d.metric in state.optimizeGoals).map((d) => ({
      metric: d.metric,
      level: state.optimizeGoals[d.metric],
    })),
    maxDetourPct: els.optimizeDetour.value,
    effort: els.optimizeEffort.value,
    detours: els.optimizeDetours.checked,
    poi: { corridorM: els.optimizePoiCorridor.value, minFame: els.optimizePoiFame.value },
  };
}

function renderOptimization(report) {
  const html = BikeOptimize.reportHtml(report);
  els.optimizationPanel.hidden = html === "";
  els.optimizationReport.innerHTML = html;
}

async function runOptimize() {
  stopPicking();
  const payload = buildRequest();
  delete payload.poi_stops; // sights are a goal here, not stops
  if (typeof payload.origin !== "object" && payload.origin === "") {
    setStatus("error", "Please enter an origin.");
    return;
  }
  if (!payload.constraints.return_to_origin && typeof payload.destination !== "object" && payload.destination === "") {
    setStatus("error", "Please enter a destination (or tick \"Return to origin\" for a loop).");
    return;
  }
  const choice = optimizeChoice();
  const built = BikeOptimize.buildSpec(choice);
  if (built.error) {
    setStatus("error", escapeHtml(built.error));
    return;
  }
  payload.optimize = built.spec;

  els.optimizeBtn.disabled = true;
  clearResults();
  if (state.aborted) state.aborted.abort();
  const controller = new AbortController();
  state.aborted = controller;
  const generation = ++state.planGeneration;
  state.planPending = generation;
  let drawn = false;
  const startedAt = performance.now();
  const wait = BikeOptimize.waitText(built.spec.effort);
  const tick = () => {
    const s = Math.round((performance.now() - startedAt) / 1000);
    setStatus(
      "loading",
      `Fetching and comparing several routes&hellip; <em>${escapeHtml(wait)}</em> (${s}&nbsp;s)`,
    );
  };
  tick();
  clearInterval(state.optimizing);
  state.optimizing = setInterval(tick, 1000);

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
    if (generation !== state.planGeneration) return; // a newer plan took over
    state.lastResponse = data;
    const secs = ((performance.now() - startedAt) / 1000).toFixed(0);
    renderResponse(data, secs);
    drawn = true;
    if (data.status === "ready") {
      renderOptimization(data.optimization);
      if (!data.optimization) {
        setStatus("info", "The route is ready, but the server returned no comparison (the optimiser is not available for this setup).");
      } else {
        revealOnPhone(els.optimizationPanel);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    setStatus("error", `Network error: ${escapeHtml(String(err))}`);
  } finally {
    clearInterval(state.optimizing);
    state.optimizing = null;
    els.optimizeBtn.disabled = false;
    settlePlan(generation, drawn);
  }
}

/** The Optimize tab exists only when the server has an engine that can return many routes. */
async function initOptimizer() {
  const caps = await getCapabilities();
  if (!caps.optimizer) return;
  state.optimizeCaps = { pois: Boolean(caps.pois) };
  els.modeTabs.hidden = false;
  els.optimizePresets.innerHTML = BikeOptimize.PRESETS.map(
    (p) => `<button type="button" class="ghost" data-preset="${p.id}">${BikeOptimize.escapeHtml(p.label)}</button>`,
  ).join("");
  renderGoals();
  setGoals(BikeOptimize.PRESETS[0].goals);
  els.optimizeHint.textContent = caps.pois
    ? ""
    : "Sights along the track need the point-of-interest service, which is off on this server.";

  els.modeTabs.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-tab-btn]");
    if (btn) setTab(btn.dataset.tabBtn);
  });
  els.optimizePresets.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-preset]");
    const preset = btn && BikeOptimize.PRESETS.find((p) => p.id === btn.dataset.preset);
    if (preset) setGoals(preset.goals);
  });
  els.optimizeGoals.addEventListener("change", (ev) => {
    const row = ev.target.closest("[data-goal]");
    if (!row) return;
    const metric = row.dataset.goal;
    const def = BikeOptimize.GOALS.find((d) => d.metric === metric);
    if (ev.target.type === "checkbox") {
      if (ev.target.checked) {
        state.optimizeGoals[metric] = row.querySelector("select").value;
        if (def.opposite) delete state.optimizeGoals[def.opposite];
      } else {
        delete state.optimizeGoals[metric];
      }
    } else if (metric in state.optimizeGoals) {
      state.optimizeGoals[metric] = ev.target.value;
    }
    applyGoalState();
  });
  els.optimizeBtn.addEventListener("click", runOptimize);
  els.optimizeResetBtn.addEventListener("click", resetAll);
  const open = (ev) => {
    const row = ev.target.closest("tr.cmp-row");
    if (row) activateCandidate(Number(row.dataset.index));
  };
  els.optimizationReport.addEventListener("click", open);
  els.optimizationReport.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      open(ev);
    }
  });

  let remembered = "plan";
  try {
    remembered = window.localStorage.getItem(TAB_KEY) || "plan";
  } catch {
    // storage blocked: start on the Plan tab
  }
  setTab(remembered);
}

/* ----------------------- remember my choices (opt-in) ----------------------- */

/** The form as the memory sees it: the constraint fields of the request, and the start place. */
function formForMemory() {
  const c = buildConstraints();
  return {
    bike_type: c.bike_type,
    prefer_surfaces: c.prefer_surfaces,
    avoid_surfaces: c.avoid_surfaces,
    avoid_high_traffic_roads: c.avoid_high_traffic_roads,
    avoid_ferries: c.avoid_ferries,
    target_distance_km: c.target_distance_km,
    max_distance_km: c.max_distance_km,
    max_ascent_m: c.max_ascent_m,
    origin: els.origin.value.trim(),
  };
}

// A browser can throw merely for *looking at* window.localStorage (blocked site data): this is then
// a storage that refuses everything, which memory.js already treats as "not remembered".
const BLOCKED_STORAGE = {
  getItem() {
    throw new Error("storage blocked");
  },
  setItem() {
    throw new Error("storage blocked");
  },
  removeItem() {
    throw new Error("storage blocked");
  },
};

function memoryStorage() {
  try {
    return window.localStorage || BLOCKED_STORAGE;
  } catch {
    return BLOCKED_STORAGE;
  }
}

/** What the settings panel says about the memory: the state, or that nothing can be stored. */
function memoryStatusText() {
  if (state.memory.enabled && state.memoryStorageFailed) {
    return "This browser would not store it (a private window or blocked storage), so nothing is remembered.";
  }
  return BikeMemory.describe(state.memory);
}

function renderMemory() {
  els.memoryEnabled.checked = state.memory.enabled;
  els.memoryKinds.hidden = !state.memory.enabled;
  els.memoryClear.hidden = !state.memory.enabled;
  els.memoryKinds.innerHTML = BikeMemory.KINDS.map(
    (k) =>
      `<label class="check"><input type="checkbox" data-kind="${k.key}" ${state.memory.keep[k.key] ? "checked" : ""} /> ${escapeHtml(k.label)}</label>`,
  ).join("");
  els.memoryStatus.textContent = memoryStatusText();
}

function storeMemory() {
  state.memoryStorageFailed = !BikeMemory.save(memoryStorage(), state.memory);
}

/** Remember what the form holds now (a no-op while memory is off). */
function rememberNow() {
  if (!state.memory.enabled) return;
  state.memory = BikeMemory.remember(state.memory, formForMemory());
  storeMemory();
  els.memoryStatus.textContent = memoryStatusText();
}

/** Put what was remembered into the form (fields that are not remembered are left alone). */
function applyRemembered() {
  const v = BikeMemory.recall(state.memory);
  const setNum = (el, value) => {
    if (value !== undefined) el.value = String(value);
  };
  if (v.bike_type && [...els.bikeType.options].some((o) => o.value === v.bike_type)) els.bikeType.value = v.bike_type;
  if (v.prefer_surfaces) els.preferSurfaces.value = v.prefer_surfaces.join(", ");
  if (v.avoid_surfaces) els.avoidSurfaces.value = v.avoid_surfaces.join(", ");
  if (v.avoid_high_traffic_roads !== undefined) els.avoidTraffic.checked = v.avoid_high_traffic_roads;
  if (v.avoid_ferries !== undefined) els.avoidFerries.checked = v.avoid_ferries;
  setNum(els.targetDistance, v.target_distance_km);
  setNum(els.maxDistance, v.max_distance_km);
  setNum(els.maxAscent, v.max_ascent_m);
  if (v.origin && !els.origin.value.trim()) {
    els.origin.value = v.origin;
    markCoordInput(els.origin);
    redrawPlaceMarkers();
  }
}

/** Remember that the form was edited, so the next chat message carries what it holds now. */
function trackFormEdits() {
  const edited = () => {
    state.formDirty = true;
  };
  for (const id of ["places-panel", "when-panel", "constraints-panel", "poi-panel", "optimize-panel"]) {
    const panel = $(id);
    if (!panel) continue;
    panel.addEventListener("input", edited);
    panel.addEventListener("change", edited);
  }
}

function initMemory() {
  state.memory = BikeMemory.load(memoryStorage());
  renderMemory();
  // A shared link is the trip somebody meant: it is not mixed with what this browser remembers.
  if (state.memory.enabled && !window.location.hash.startsWith(`#${BikeLink.KEY}=`)) applyRemembered();
  let timer = null;
  const later = () => {
    state.memoryHoldBack = false; // the person is editing the form: it is theirs again
    clearTimeout(timer);
    timer = setTimeout(rememberNow, 600);
  };
  for (const el of [els.bikeType, els.targetDistance, els.maxDistance, els.maxAscent, els.preferSurfaces, els.avoidSurfaces, els.avoidTraffic, els.avoidFerries, els.origin]) {
    el.addEventListener("input", later);
    el.addEventListener("change", later);
  }
  els.memoryEnabled.addEventListener("change", () => {
    state.memory = BikeMemory.setEnabled(state.memory, els.memoryEnabled.checked);
    if (state.memory.enabled) rememberNow(); // what is in the form now is the first thing remembered
    else BikeMemory.erase(memoryStorage());
    renderMemory();
  });
  els.memoryKinds.addEventListener("change", (ev) => {
    const box = ev.target.closest("input[data-kind]");
    if (!box) return;
    state.memory = BikeMemory.setKept(state.memory, box.dataset.kind, box.checked);
    rememberNow(); // a kind just ticked takes what the form holds
    renderMemory();
  });
  els.memoryClear.addEventListener("click", () => {
    state.memory = { ...state.memory, values: {} };
    storeMemory();
    renderMemory();
  });
}

/* --------------------------- settings panel (right) --------------------------- */

function updateSettingsEmpty() {
  const groups = [...els.settingsPanel.querySelectorAll(".settings-group")];
  els.settingsEmpty.hidden = !BikeSettings.isEmpty(groups.map((g) => g.hidden));
}

function setSettingsOpen(open, restoreFocus) {
  const v = BikeSettings.view(open);
  els.settingsPanel.classList.toggle("open", open);
  els.settingsPanel.setAttribute("aria-hidden", v.hidden);
  els.settingsPanel.inert = !open; // out of the tab order while hidden
  els.settingsToggle.setAttribute("aria-expanded", v.expanded);
  els.settingsToggle.setAttribute("aria-label", v.label);
  els.settingsToggle.title = open ? "Close settings" : "Settings";
  if (open) els.settingsClose.focus();
  else if (restoreFocus) els.settingsToggle.focus();
}

function initSettingsPanel() {
  updateSettingsEmpty();
  els.settingsToggle.addEventListener("click", () => {
    setSettingsOpen(!els.settingsPanel.classList.contains("open"), true);
  });
  els.settingsClose.addEventListener("click", () => setSettingsOpen(false, true));
  document.addEventListener("keydown", (ev) => {
    if (BikeSettings.closesOnKey(ev.key, els.settingsPanel.classList.contains("open"))) {
      setSettingsOpen(false, true);
    }
  });
}

/* --------------------------------- boot ---------------------------------- */

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initDeparture();
  wireEvents();
  initTextPlanning();
  initSettingsPanel();
  initPois();
  trackFormEdits();
  initMemory(); // before the link: a link overrides it, but the controls must exist either way
  loadSharedPlan(); // before the chat restores its route: a link's trip is the newest
  // A link pasted into the address bar of a page that is already open is only a fragment change.
  window.addEventListener("hashchange", loadSharedPlan);
  initChat();
  initStatus();
  initOptimizer();
});
