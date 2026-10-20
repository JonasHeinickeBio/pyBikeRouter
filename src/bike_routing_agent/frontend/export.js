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

  /* ------------------------------- the forecast ------------------------------ */
  // Mirrors exporters/forecast.py: the same wording and the same rounding (Math.round).

  const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const WIND_NOTE_KMH = 3;

  const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
  const gpxTime = (iso) => `${new Date(iso).toISOString().slice(0, 19)}Z`;
  const clock = (iso) => new Date(iso).toISOString().slice(11, 16);

  function sampleDescription(sample) {
    const hour = sample.weather || {};
    const parts = [];
    if (hour.temperature_c != null) parts.push(`${Math.round(hour.temperature_c)} \u00b0C`);
    if (hour.precipitation_probability != null) parts.push(`rain ${Math.round(hour.precipitation_probability)} %`);
    if (hour.wind_speed_kmh != null && hour.wind_from_deg != null) {
      parts.push(`wind ${Math.round(hour.wind_speed_kmh)} km/h from ${compass(hour.wind_from_deg)}`);
    }
    const head = sample.headwind_kmh;
    if (head != null && Math.abs(head) >= WIND_NOTE_KMH) {
      parts.push(`${head > 0 ? "headwind" : "tailwind"} ${Math.round(Math.abs(head))} km/h`);
    }
    return parts.join(", ");
  }

  function windEffectText(effect) {
    if (!effect || effect.effect_s == null) return null;
    const minutes = Math.round(Math.abs(effect.effect_s) / 60);
    return minutes === 0 ? null : `${effect.effect_s < 0 ? "-" : "+"}${minutes} min`;
  }

  function forecastDescription(weather) {
    const left = new Date(weather.departure).toISOString().slice(0, 16).replace("T", " ");
    let text = `Forecast for a ride leaving ${left} UTC.`;
    const effect = windEffectText(weather.wind_effect);
    if (effect) text += ` Estimated effect of the wind on the time: ${effect}.`;
    return weather.attribution ? `${text} ${weather.attribution}` : text;
  }

  /** The candidate's forecast, or null without one (a forecast with no points counts as none). */
  function weatherOf(candidate) {
    const weather = candidate.weather;
    return weather && Array.isArray(weather.samples) && weather.samples.length ? weather : null;
  }

  function forecastWaypoints(weather) {
    return weather.samples
      .map((s) => {
        const text = sampleDescription(s);
        return (
          `<wpt lon="${s.lon}" lat="${s.lat}"><time>${gpxTime(s.time)}</time>` +
          `<name>${xmlEscape(`Forecast ${clock(s.time)} UTC`)}</name>` +
          (text ? `<desc>${xmlEscape(text)}</desc>` : "") +
          "</wpt>"
        );
      })
      .join("");
  }

  function forecastProperties(weather) {
    const summary = weather.summary || {};
    const orNull = (v) => (v === undefined ? null : v);
    const keys = [
      "temperature_min_c",
      "temperature_max_c",
      "precipitation_probability_max",
      "wind_speed_max_kmh",
      "wind_gust_max_kmh",
      "headwind_mean_kmh",
    ];
    return {
      departure: weather.departure,
      arrival: weather.arrival,
      attribution: orNull(weather.attribution),
      summary: Object.fromEntries(keys.map((k) => [k, orNull(summary[k])])),
      wind_effect: orNull(weather.wind_effect),
      advisories: weather.advisories || [],
      points: weather.samples.map((s) => {
        const hour = s.weather || {};
        return {
          fraction: s.fraction,
          lon: s.lon,
          lat: s.lat,
          time: s.time,
          temperature_c: orNull(hour.temperature_c),
          precipitation_probability: orNull(hour.precipitation_probability),
          wind_speed_kmh: orNull(hour.wind_speed_kmh),
          wind_from_deg: orNull(hour.wind_from_deg),
          headwind_kmh: orNull(s.headwind_kmh),
          condition: orNull(hour.condition),
        };
      }),
    };
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
    const weather = weatherOf(candidate);
    return (
      '<?xml version="1.0" encoding="UTF-8"?>\n' +
      `<gpx version="1.1" creator="bike-routing-agent" xmlns="${GPX_NS}">` +
      `<metadata><name>${title}</name>` +
      (weather ? `<desc>${xmlEscape(forecastDescription(weather))}</desc>` : "") +
      "<extensions>" +
      `<provider>${xmlEscape(candidate.provider)}</provider>` +
      `<provider_profile>${xmlEscape(candidate.provider_profile)}</provider_profile>` +
      "</extensions></metadata>" +
      (weather ? forecastWaypoints(weather) : "") +
      `<trk><name>${title}</name>${segments}</trk></gpx>`
    );
  }

  /** GeoJSON Feature with the same properties the server writes. */
  function geojson(candidate) {
    lines(candidate.geometry_geojson); // same validation as the server
    const m = candidate.metrics || {};
    const orNull = (v) => (v === undefined ? null : v);
    const properties = {
      provider: candidate.provider,
      provider_profile: candidate.provider_profile,
      distance_m: orNull(m.distance_m),
      duration_s: orNull(m.duration_s),
      ascent_m: orNull(m.ascent_m),
      descent_m: orNull(m.descent_m),
      score: orNull(candidate.score),
      warnings: candidate.warnings || [],
    };
    const weather = weatherOf(candidate);
    if (weather) properties.forecast = forecastProperties(weather);
    return JSON.stringify({ type: "Feature", geometry: candidate.geometry_geojson, properties }, null, 2);
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

  const api = { gpx, geojson, filename, build, xmlEscape, sampleDescription, forecastDescription, compass };
  root.BikeExport = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
