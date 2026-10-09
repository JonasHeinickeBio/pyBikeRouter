/* Download any candidate route as GPX or GeoJSON (pure functions, no DOM access).
 *
 * The server writes files for the top-ranked route only; with alternatives on
 * screen a rider may prefer another one, so the same two formats are built here
 * from the candidate already in the response. Kept deliberately in step with
 * exporters/gpx.py and exporters/geojson.py (a test compares both implementations).
 */
(function (root) {
  "use strict";

  const GPX_NS = "http://www.topografix.com/GPX/1/1";

  function xmlEscape(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** The vertex lists of a (Multi)LineString; throws for any other geometry, like the server. */
  function lines(geometry) {
    if (geometry && geometry.type === "LineString") return [geometry.coordinates];
    if (geometry && geometry.type === "MultiLineString") return geometry.coordinates;
    throw new Error(`unsupported geometry type for export: ${geometry && geometry.type}`);
  }

  /** GPX 1.1 track of a candidate, with elevation when the geometry carries it. */
  function gpx(candidate, name) {
    const title = xmlEscape(name || "route");
    const segments = lines(candidate.geometry_geojson)
      .map(
        (line) =>
          "<trkseg>" +
          line
            .map((p) => {
              const ele = p.length > 2 ? `<ele>${p[2]}</ele>` : "";
              return `<trkpt lon="${p[0]}" lat="${p[1]}">${ele}</trkpt>`;
            })
            .join("") +
          "</trkseg>",
      )
      .join("");
    return (
      '<?xml version="1.0" encoding="UTF-8"?>\n' +
      `<gpx version="1.1" creator="bike-routing-agent" xmlns="${GPX_NS}">` +
      `<metadata><name>${title}</name><extensions>` +
      `<provider>${xmlEscape(candidate.provider)}</provider>` +
      `<provider_profile>${xmlEscape(candidate.provider_profile)}</provider_profile>` +
      "</extensions></metadata>" +
      `<trk><name>${title}</name>${segments}</trk></gpx>`
    );
  }

  /** GeoJSON Feature with the same properties the server writes. */
  function geojson(candidate) {
    lines(candidate.geometry_geojson); // same validation as the server
    const m = candidate.metrics || {};
    const orNull = (v) => (v === undefined ? null : v);
    return JSON.stringify(
      {
        type: "Feature",
        geometry: candidate.geometry_geojson,
        properties: {
          provider: candidate.provider,
          provider_profile: candidate.provider_profile,
          distance_m: orNull(m.distance_m),
          duration_s: orNull(m.duration_s),
          ascent_m: orNull(m.ascent_m),
          descent_m: orNull(m.descent_m),
          score: orNull(candidate.score),
          warnings: candidate.warnings || [],
        },
      },
      null,
      2,
    );
  }

  /** "route-custom_touring-v1-22.7km.gpx": a name that says which alternative it is. */
  function filename(candidate, extension) {
    const km = (candidate.metrics && typeof candidate.metrics.distance_m === "number")
      ? `-${(candidate.metrics.distance_m / 1000).toFixed(1)}km`
      : "";
    const profile = String(candidate.provider_profile || candidate.provider || "route")
      .replace(/[^A-Za-z0-9._-]+/g, "_");
    return `route-${profile}${km}.${extension}`;
  }

  /** The text, MIME type and file name for one download. */
  function build(candidate, format, name) {
    if (format === "gpx") {
      return { text: gpx(candidate, name), type: "application/gpx+xml", filename: filename(candidate, "gpx") };
    }
    if (format === "geojson") {
      return { text: geojson(candidate), type: "application/geo+json", filename: filename(candidate, "geojson") };
    }
    throw new Error(`unknown export format: ${format}`);
  }

  const api = { gpx, geojson, filename, build, xmlEscape };
  root.BikeExport = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
