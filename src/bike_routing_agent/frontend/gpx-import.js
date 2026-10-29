/* Read a GPX file into a route the page can show (pure functions, no DOM access).
 *
 * The file is read in the browser and never sent anywhere: the track is drawn like a planned route
 * (map, elevation profile, points of interest along it), but nothing was routed, so it carries no
 * score, surface or traffic data. Distance and ascent are measured from the points in the file.
 *
 * A small tolerant reader instead of DOMParser, so the same code runs under node in the tests. GPX
 * is flat and well known (gpx > trk > trkseg > trkpt, rte > rtept, wpt); a DOCTYPE is refused.
 */
(function (root) {
  "use strict";

  const MAX_BYTES = 25 * 1024 * 1024; // larger than any ride a head unit records
  const MAX_DRAWN_POINTS = 20000; // a longer track is thinned for the map; its metrics use every point
  const ASCENT_STEP_M = 3; // a rise or fall counts once it is this big: GPS and barometer noise is not climbing
  const LOOP_CLOSE_M = 150; // a track that ends this close to where it began is a loop
  const EARTH_RADIUS_M = 6371008.8;

  class GpxError extends Error {}

  /* --------------------------------- reading XML -------------------------------- */

  const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

  function decode(text) {
    return text.replace(/&(?:#(\d+)|#x([0-9a-f]+)|(amp|lt|gt|quot|apos));/gi, (all, dec, hex, name) => {
      if (name) return ENTITIES[name.toLowerCase()];
      const code = dec ? parseInt(dec, 10) : parseInt(hex, 16);
      return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : "";
    });
  }

  const localName = (name) => name.slice(name.lastIndexOf(":") + 1).toLowerCase();

  // A comment, CDATA, a declaration or processing instruction, a tag (with its attributes), or text.
  const TOKEN =
    /<!--[\s\S]*?-->|<!\[CDATA\[([\s\S]*?)\]\]>|<\?[\s\S]*?\?>|<(\/?)([^\s/>!?]+)((?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(\/?)>|([^<]+)/g;
  const ATTRIBUTE = /([^\s=]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;

  function parseXml(source) {
    const xml = String(source).replace(/^﻿/, "");
    if (/<!DOCTYPE/i.test(xml)) throw new GpxError("This file declares a DOCTYPE, which a GPX file does not need; not reading it.");
    const top = { name: "#document", attrs: {}, children: [], text: "" };
    const stack = [top];
    TOKEN.lastIndex = 0;
    for (let m = TOKEN.exec(xml); m; m = TOKEN.exec(xml)) {
      const current = stack[stack.length - 1];
      if (m[1] !== undefined) {
        current.text += m[1];
      } else if (m[6] !== undefined) {
        current.text += decode(m[6]);
      } else if (m[3] !== undefined) {
        const name = localName(m[3]);
        if (m[2]) {
          if (stack.length < 2 || current.name !== name) throw new GpxError("This is not well-formed XML (a tag is closed that was not open).");
          stack.pop();
        } else {
          const attrs = {};
          for (const a of (m[4] || "").matchAll(ATTRIBUTE)) {
            attrs[localName(a[1])] = decode(a[2] !== undefined ? a[2] : a[3]);
          }
          const node = { name, attrs, children: [], text: "" };
          current.children.push(node);
          if (!m[5]) stack.push(node);
        }
      }
    }
    if (stack.length !== 1) throw new GpxError("This is not complete XML (the file ends inside a tag; was it cut off?).");
    return top;
  }

  const kids = (node, name) => node.children.filter((c) => c.name === name);
  const kid = (node, name) => node.children.find((c) => c.name === name) || null;
  const textOf = (node, name) => {
    const child = kid(node, name);
    return child ? child.text.trim() : "";
  };

  /* --------------------------------- the points --------------------------------- */

  // A decimal number as XML writes it. Number() alone reads "" and " " as 0 and "0x10" as 16.
  const DECIMAL = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
  const decimal = (text) => (text !== undefined && DECIMAL.test(text.trim()) ? Number(text) : NaN);

  // xsd:dateTime as GPX writes it (an offset is optional there, and "Z" is what devices write).
  const ISO_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$/;

  /**
   * Milliseconds for an ISO 8601 date-time, or NaN for any other text. Date.parse alone reads "5" as a
   * date and rolls 2026-02-30 over into March. A time without an offset is read as UTC, so two such
   * points still give their true difference (and never the viewer's time zone or a DST change).
   */
  function parseTime(text) {
    const m = ISO_TIME.exec(text.trim());
    if (!m) return NaN;
    const [year, month, day, hour, minute, second] = m.slice(1, 7).map(Number);
    const offset = m[7];
    if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return NaN;
    if (hour > 23 || minute > 59 || second > 59) return NaN;
    if (offset && offset !== "Z" && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4, 6)) > 59)) return NaN;
    return Date.parse(offset ? text.trim() : `${text.trim()}Z`);
  }

  /** [lon, lat, ele|null, time(ms)|null], or null for a point that is not on Earth. */
  function readPoint(node) {
    const lat = decimal(node.attrs.lat);
    const lon = decimal(node.attrs.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    const elevation = decimal(textOf(node, "ele"));
    const time = parseTime(textOf(node, "time"));
    return [lon, lat, Number.isFinite(elevation) ? elevation : null, Number.isFinite(time) ? time : null];
  }

  function haversine(a, b) {
    const rad = Math.PI / 180;
    const dLat = (b[1] - a[1]) * rad;
    const dLon = (b[0] - a[0]) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function lengthOf(points) {
    let total = 0;
    for (let i = 1; i < points.length; i++) total += haversine(points[i - 1], points[i]);
    return total;
  }

  /** Climbing and descending that counts: a change must reach ASCENT_STEP_M before it is added. */
  function climb(elevations) {
    let ascent = 0;
    let descent = 0;
    let reference = elevations[0];
    for (const e of elevations) {
      const change = e - reference;
      if (change >= ASCENT_STEP_M) {
        ascent += change;
        reference = e;
      } else if (change <= -ASCENT_STEP_M) {
        descent -= change;
        reference = e;
      }
    }
    return { ascent, descent };
  }

  /** Every `step`-th point, always keeping the last: the shape on the map, not the measurements. */
  function thin(points, limit) {
    if (points.length <= limit) return points;
    const step = Math.ceil(points.length / limit);
    const out = points.filter((_, i) => i % step === 0);
    if (out[out.length - 1] !== points[points.length - 1]) out.push(points[points.length - 1]);
    return out;
  }

  /* ----------------------------------- parse ------------------------------------ */

  /**
   * What a GPX file holds: { name, points, segments, waypoints, loop, distance_m, ascent_m,
   * descent_m, duration_s, warnings }. Throws a GpxError, with a sentence for a person, when the
   * text is not a GPX file with a track (or route) in it.
   */
  function parse(text) {
    const document = parseXml(text);
    const gpx = kid(document, "gpx");
    if (!gpx) throw new GpxError("This does not look like a GPX file (there is no <gpx> element).");

    const warnings = [];
    let skipped = 0;
    const collect = (nodes) => {
      const out = [];
      for (const n of nodes) {
        const p = readPoint(n);
        if (p) out.push(p);
        else skipped += 1;
      }
      return out;
    };

    let segments = kids(gpx, "trk").flatMap((trk) => kids(trk, "trkseg")).map((seg) => collect(kids(seg, "trkpt")));
    let kind = "track";
    if (!segments.some((s) => s.length >= 2)) {
      segments = kids(gpx, "rte").map((rte) => collect(kids(rte, "rtept")));
      kind = "route";
    }
    segments = segments.filter((s) => s.length > 0);
    const points = segments.flat();
    if (points.length < 2) {
      throw new GpxError("There is no track or route with at least two points in this file.");
    }

    const named = kind === "track" ? kids(gpx, "trk") : kids(gpx, "rte");
    const meta = kid(gpx, "metadata");
    const name = (named.length && textOf(named[0], "name")) || (meta && textOf(meta, "name")) || "";

    if (segments.length > 1) {
      warnings.push(`The file has ${segments.length} separate ${kind === "track" ? "segments" : "routes"}; they are joined in order, and a gap between them is drawn as a straight line.`);
    }
    if (skipped) warnings.push(`${skipped} point${skipped === 1 ? "" : "s"} without a usable position ${skipped === 1 ? "was" : "were"} left out.`);

    const waypoints = kids(gpx, "wpt")
      .map((w) => ({ point: readPoint(w), name: textOf(w, "name"), description: textOf(w, "desc") }))
      .filter((w) => w.point)
      .map((w) => ({ lon: w.point[0], lat: w.point[1], name: w.name, description: w.description }));

    const elevations = points.map((p) => p[2]);
    const complete = elevations.every((e) => e !== null);
    let ascent = null;
    let descent = null;
    if (complete) {
      ({ ascent, descent } = climb(elevations));
    } else if (elevations.some((e) => e !== null)) {
      warnings.push("Some points have no elevation, so the elevation is left out (a profile with holes would mislead).");
    }

    const first = points[0];
    const last = points[points.length - 1];
    const timed = first[3] !== null && last[3] !== null && last[3] > first[3];

    return {
      name,
      kind,
      points: points.map((p) => (complete ? [p[0], p[1], p[2]] : [p[0], p[1]])),
      segments: segments.length,
      waypoints,
      loop: haversine(first, last) <= LOOP_CLOSE_M,
      distance_m: lengthOf(points),
      ascent_m: ascent,
      descent_m: descent,
      duration_s: timed ? (last[3] - first[3]) / 1000 : null,
      warnings,
    };
  }

  /* --------------------------------- the page's shape --------------------------------- */

  const baseName = (filename) => String(filename || "").replace(/^.*[\\/]/, "").replace(/\.(?:gpx|xml)$/i, "");
  const round = (v, digits) => (v === null ? null : Math.round(v * 10 ** digits) / 10 ** digits);
  const km = (m) => `${(m / 1000).toFixed(m >= 100000 ? 0 : 1)} km`;

  function elapsed(seconds) {
    const minutes = Math.round(seconds / 60);
    return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
  }

  /**
   * The parsed file as the plan response the results panel already knows how to show: one ready
   * candidate (provider "gpx"), no score, no alternatives, no files from the server.
   */
  function toResponse(file, filename) {
    const label = file.name || baseName(filename) || "imported track";
    const warnings = file.warnings.slice();
    const drawn = thin(file.points, MAX_DRAWN_POINTS);
    if (drawn.length < file.points.length) {
      warnings.push(`The track has ${file.points.length} points; ${drawn.length} are drawn, and the distance and climb use all of them.`);
    }
    const candidate = {
      provider: "gpx",
      provider_profile: label,
      geometry_geojson: { type: "LineString", coordinates: drawn },
      metrics: {
        distance_m: round(file.distance_m, 0),
        duration_s: round(file.duration_s, 0),
        ascent_m: round(file.ascent_m, 0),
        descent_m: round(file.descent_m, 0),
      },
      score: null,
      warnings,
      provenance: { imported_from: baseName(filename) || null, points: file.points.length, waypoints: file.waypoints.length },
      rank: null,
      pros: [],
      cons: [],
      duplicates: [],
      duplicate_of: null,
    };
    const parts = [`Imported "${label}": ${km(file.distance_m)}`];
    if (file.ascent_m !== null) parts.push(`${Math.round(file.ascent_m)} m up`);
    if (file.duration_s !== null) parts.push(`${elapsed(file.duration_s)} recorded, stops included`);
    const notes = [
      "Nothing was routed: the track is drawn as it is in the file. Distance and climb are measured from its points" +
        (file.ascent_m !== null ? ` (a rise or fall counts from ${ASCENT_STEP_M} m)` : "") +
        ", so there is no score, surface or traffic information.",
    ];
    if (file.waypoints.length) notes.push(`${file.waypoints.length} waypoint${file.waypoints.length === 1 ? "" : "s"} from the file ${file.waypoints.length === 1 ? "is" : "are"} marked on the map.`);
    return {
      status: "ready",
      route: candidate,
      candidates: [candidate],
      explanation: `${parts.join(", ")}. ${notes.join(" ")}`,
      errors: [],
      artifacts: [],
      weather_status: null,
      imported: { filename: baseName(filename) || null, loop: file.loop, waypoints: file.waypoints },
    };
  }

  /** Why a file is not read before it is opened, or null when it may be. */
  function refusal(file) {
    if (!file) return "No file was chosen.";
    if (file.size > MAX_BYTES) return `That file is ${(file.size / 1048576).toFixed(0)} MB; a GPX track of up to ${MAX_BYTES / 1048576} MB is read.`;
    return null;
  }

  /** A person's sentence for any failure to read a file. */
  function errorText(error) {
    return error instanceof GpxError ? error.message : "That file could not be read.";
  }

  const api = { parse, toResponse, refusal, errorText, GpxError, MAX_BYTES, MAX_DRAWN_POINTS, ASCENT_STEP_M, LOOP_CLOSE_M };
  root.BikeGpxImport = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
