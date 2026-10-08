/* Weather presentation helpers (pure functions, no DOM access).
 *
 * app.js renders the card and the map markers with these; keeping them free of
 * the DOM lets them be unit-tested under node (tests/test_frontend_weather.py).
 * All facts shown come from the API's `weather` object: nothing is computed
 * here beyond formatting, and nothing is worded as a safety judgement.
 */
(function (root) {
  "use strict";

  const CONDITIONS = {
    clear: { icon: "☀️", night: "🌙", label: "Clear" },
    partly_cloudy: { icon: "⛅", night: "☁️", label: "Partly cloudy" },
    cloudy: { icon: "☁️", night: "☁️", label: "Cloudy" },
    fog: { icon: "🌫️", night: "🌫️", label: "Fog" },
    drizzle: { icon: "🌦️", night: "🌧️", label: "Drizzle" },
    rain: { icon: "🌧️", night: "🌧️", label: "Rain" },
    freezing_rain: { icon: "🧊", night: "🧊", label: "Freezing rain" },
    snow: { icon: "❄️", night: "❄️", label: "Snow" },
    thunderstorm: { icon: "⛈️", night: "⛈️", label: "Thunderstorms" },
    unknown: { icon: "·", night: "·", label: "No conditions reported" },
  };

  const PROVIDER_SITES = {
    "open-meteo": { name: "Open-Meteo", url: "https://open-meteo.com/" },
    "met-no": { name: "MET Norway", url: "https://api.met.no/" },
    dwd: { name: "Deutscher Wetterdienst", url: "https://www.dwd.de/" },
  };

  const COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
  ];

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function conditionInfo(condition, isDay) {
    const info = CONDITIONS[condition] || CONDITIONS.unknown;
    return { icon: isDay === false ? info.night : info.icon, label: info.label };
  }

  /** 16-point compass name of a bearing in degrees. */
  function compass(deg) {
    const normalized = ((deg % 360) + 360) % 360;
    return COMPASS[Math.round(normalized / 22.5) % 16];
  }

  /** Mean of directions (degrees) as a vector average, so 350 and 10 give 0, not 180. */
  function circularMean(degrees) {
    const valid = degrees.filter((d) => typeof d === "number" && Number.isFinite(d));
    if (!valid.length) return null;
    let x = 0;
    let y = 0;
    for (const d of valid) {
      x += Math.cos((d * Math.PI) / 180);
      y += Math.sin((d * Math.PI) / 180);
    }
    if (Math.abs(x) < 1e-9 && Math.abs(y) < 1e-9) return null; // opposing winds cancel out
    return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
  }

  /** Arrow pointing where the wind blows TO (a wind from the north points down). */
  function windArrow(windFromDeg, sizePx) {
    const size = sizePx || 18;
    if (typeof windFromDeg !== "number" || !Number.isFinite(windFromDeg)) return "";
    const blowingTo = (windFromDeg + 180) % 360;
    return (
      `<svg class="wind-arrow" width="${size}" height="${size}" viewBox="0 0 24 24" ` +
      `role="img" aria-label="wind from ${compass(windFromDeg)}" ` +
      `style="transform: rotate(${blowingTo.toFixed(0)}deg)">` +
      '<path d="M12 2 L19 21 L12 16.5 L5 21 Z" fill="currentColor"/></svg>'
    );
  }

  function num(value, digits) {
    return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits || 0) : null;
  }

  /** "9–14 °C", "12 °C", or null when the forecast has no temperature. */
  function temperatureRange(summary) {
    const lo = num(summary.temperature_min_c);
    const hi = num(summary.temperature_max_c);
    if (lo === null || hi === null) return null;
    return lo === hi ? `${lo} °C` : `${lo}–${hi} °C`;
  }

  /** "Feels like 2–5 °C", only when it differs from the air temperature by 2 °C or more. */
  function feelsLike(summary) {
    const lo = num(summary.apparent_temperature_min_c);
    const hi = num(summary.apparent_temperature_max_c);
    if (lo === null || hi === null) return null;
    const airLo = summary.temperature_min_c;
    const airHi = summary.temperature_max_c;
    if (
      typeof airLo === "number" &&
      typeof airHi === "number" &&
      Math.abs(summary.apparent_temperature_min_c - airLo) < 2 &&
      Math.abs(summary.apparent_temperature_max_c - airHi) < 2
    ) {
      return null;
    }
    return lo === hi ? `${lo} °C` : `${lo}–${hi} °C`;
  }

  /** Where and when the rain is: "km 12–31 (14:00–16:00)", or null for a dry forecast. */
  function wetStretchText(stretch) {
    if (!stretch) return null;
    const when = `${clock(stretch.from_time)}–${clock(stretch.to_time)}`;
    if (stretch.whole_route) return `along the whole route (${when})`;
    if (typeof stretch.from_km !== "number" || typeof stretch.to_km !== "number") {
      return `part of the route (${when})`;
    }
    const a = Math.round(stretch.from_km);
    const b = Math.round(stretch.to_km);
    return a === b ? `around km ${a} (${when})` : `km ${a}–${b} (${when})`;
  }

  /** "Sunrise 07:12 · sunset 18:31" in the viewer's local time, or null. */
  function daylightText(daylight) {
    if (!daylight) return null;
    return `${clock(daylight.sunrise)} / ${clock(daylight.sunset)}`;
  }

  /** Mean head/tailwind as {kind, text}; calm below 3 km/h. */
  function windVsRoute(meanHeadwind) {
    if (typeof meanHeadwind !== "number" || !Number.isFinite(meanHeadwind)) return null;
    const speed = Math.abs(meanHeadwind);
    if (speed < 3) return { kind: "calm", text: "wind mostly across the route" };
    return meanHeadwind > 0
      ? { kind: "head", text: `${speed.toFixed(0)} km/h headwind on average` }
      : { kind: "tail", text: `${speed.toFixed(0)} km/h tailwind on average` };
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  /** Local clock time of an ISO instant: "14:30". */
  function clock(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /** "Wed 14:30" in the viewer's local time. */
  function dayAndClock(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const day = d.toLocaleDateString(undefined, { weekday: "short" });
    return `${day} ${clock(iso)}`;
  }

  /** The licence credit plus a link for every source that contributed to the forecast. */
  function providerCredit(weather) {
    const names =
      Array.isArray(weather.sources) && weather.sources.length ? weather.sources : [weather.provider];
    const sites = names.map((n) => PROVIDER_SITES[n]).filter(Boolean);
    if (!sites.length) return escapeHtml(weather.attribution || "");
    const text = escapeHtml(weather.attribution || `Weather data by ${sites[0].name}`);
    const links = sites
      .map((s) => `<a href="${s.url}" target="_blank" rel="noopener">${s.name}</a>`)
      .join(" &middot; ");
    return `${text} &middot; ${links}`;
  }

  function stripItem(sample) {
    const w = sample.weather;
    const info = conditionInfo(w.condition, w.is_day);
    const temp = num(w.temperature_c);
    const speed = num(w.wind_speed_kmh);
    const rain = num(w.precipitation_probability);
    return (
      '<li class="strip-item">' +
      `<span class="strip-time">${escapeHtml(clock(sample.time))}</span>` +
      `<span class="strip-icon" role="img" aria-label="${escapeHtml(info.label)}">${info.icon}</span>` +
      `<span class="strip-temp">${temp === null ? "—" : `${temp}°`}</span>` +
      `<span class="strip-wind">${windArrow(w.wind_from_deg, 14)}${speed === null ? "" : speed}</span>` +
      `<span class="strip-rain">${rain === null ? "" : `${rain}%`}</span>` +
      "</li>"
    );
  }

  const STATUS_NOTES = {
    unavailable: "The weather forecast could not be loaded right now. The route itself is unaffected.",
    not_covered: "The forecast does not cover this ride (it may be too far ahead).",
  };

  /** Card HTML for one candidate's weather, or "" when there is nothing to say. */
  function weatherCardHtml(weather, status) {
    if (!weather) {
      const note = STATUS_NOTES[status];
      return note ? `<p class="weather-note">${escapeHtml(note)}</p>` : "";
    }
    const s = weather.summary || {};
    const info = conditionInfo(s.worst_condition, s.after_dark ? false : undefined);
    const temp = temperatureRange(s);
    const meanFrom = circularMean((weather.samples || []).map((x) => x.weather.wind_from_deg));
    const vs = windVsRoute(s.headwind_mean_kmh);
    const facts = [];

    const rain = num(s.precipitation_probability_max);
    if (rain !== null) facts.push(["Rain chance", `up to ${rain}%`]);
    const wind = num(s.wind_speed_max_kmh);
    if (wind !== null) {
      const gust = num(s.wind_gust_max_kmh);
      const from = meanFrom === null ? "" : ` from ${compass(meanFrom)}`;
      facts.push(["Wind", `${wind} km/h${from}${gust ? `, gusts ${gust}` : ""}`]);
    }
    const uv = num(s.uv_index_max);
    if (uv !== null && Number(uv) > 0) facts.push(["UV index", `up to ${uv}`]);
    const feels = feelsLike(s);
    if (feels !== null) facts.push(["Feels like", feels]);
    const wet = wetStretchText(s.wet_stretch);
    if (wet !== null) facts.push(["Precipitation", wet]);
    const sun = daylightText(weather.daylight);
    if (sun !== null) facts.push(["Sunrise / sunset", sun]);

    const headShare = typeof s.headwind_share === "number" ? s.headwind_share : null;
    const tailShare = typeof s.tailwind_share === "number" ? s.tailwind_share : null;
    const bar =
      headShare === null || tailShare === null
        ? ""
        : '<div class="windbar" role="img" aria-label="' +
          `headwind on ${Math.round(headShare * 100)}% of the route, tailwind on ${Math.round(tailShare * 100)}%">` +
          `<span class="head" style="width:${(headShare * 100).toFixed(0)}%"></span>` +
          `<span class="tail" style="width:${(tailShare * 100).toFixed(0)}%"></span></div>` +
          '<div class="windbar-legend"><span class="lg-head">headwind</span>' +
          '<span class="lg-tail">tailwind</span></div>';

    const notes = (weather.advisories || [])
      .map((a) => `<li>${escapeHtml(a)}</li>`)
      .join("");
    const estimated =
      weather.duration_source === "estimated"
        ? '<p class="weather-note">Ride times are estimated from the distance, so the forecast hours are approximate.</p>'
        : "";

    return (
      '<div class="weather-head">' +
      `<span class="weather-icon" role="img" aria-label="${escapeHtml(info.label)}">${info.icon}</span>` +
      "<div>" +
      `<div class="weather-temp">${temp === null ? "Forecast" : escapeHtml(temp)}</div>` +
      `<div class="weather-sub">${escapeHtml(info.label)} &middot; departing ${escapeHtml(dayAndClock(weather.departure))}</div>` +
      "</div></div>" +
      (facts.length
        ? '<dl class="weather-facts">' +
          facts.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("") +
          "</dl>"
        : "") +
      (vs
        ? `<p class="wind-vs ${vs.kind}">${meanFrom === null ? "" : windArrow(meanFrom, 16)}${escapeHtml(vs.text)}</p>`
        : "") +
      bar +
      `<ol class="weather-strip" aria-label="Forecast along the route">${(weather.samples || []).map(stripItem).join("")}</ol>` +
      (notes ? `<ul class="weather-notes">${notes}</ul>` : "") +
      departureOptionsHtml(weather) +
      estimated +
      `<p class="weather-credit">${providerCredit(weather)}</p>`
    );
  }

  /** "+2 h" / "-1 h" / "requested" relative to the departure you asked for. */
  function offsetLabel(minutes) {
    if (minutes === 0) return "your time";
    const hours = Math.abs(minutes) / 60;
    return `${minutes > 0 ? "+" : "−"}${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
  }

  /** One departure option as a button row: time, rain, wind, daylight. */
  function departureOptionHtml(option, suggestedIso) {
    const raining = option.wet_samples > 0;
    const rain = raining
      ? `<span class="opt-rain wet" title="${option.wet_samples} of ${option.samples} forecast points wet">` +
        `💧 ${option.wet_samples}/${option.samples}</span>`
      : '<span class="opt-rain dry" title="No precipitation forecast along the route">dry</span>';
    const head = windCell({ summary: { headwind_mean_kmh: option.headwind_mean_kmh } });
    const dark = option.after_dark
      ? '<span class="opt-dark" title="Starts before sunrise or ends after sunset">🌙</span>'
      : "";
    const isSuggested = suggestedIso && new Date(suggestedIso).getTime() === new Date(option.departure).getTime();
    const classes = ["opt-row"];
    if (option.offset_minutes === 0) classes.push("requested");
    if (isSuggested) classes.push("suggested");
    return (
      `<li><button type="button" class="${classes.join(" ")}" data-departure="${escapeHtml(option.departure)}" ` +
      `title="Plan this route for a ${escapeHtml(clock(option.departure))} departure">` +
      `<span class="opt-time">${escapeHtml(clock(option.departure))}</span>` +
      `<span class="opt-offset">${offsetLabel(option.offset_minutes)}</span>` +
      rain +
      `<span class="opt-wind wind-${head.kind}" title="${escapeHtml(head.title)}">${escapeHtml(head.text)}</span>` +
      dark +
      (isSuggested ? '<span class="opt-tag">suggested</span>' : "") +
      "</button></li>"
    );
  }

  /** "Other departure times" list, or "" when the forecast has no comparison. */
  function departureOptionsHtml(weather) {
    const options = (weather && weather.departure_options) || [];
    if (options.length < 2) return "";
    return (
      '<details class="departure-options"' +
      (weather.suggested_departure ? " open" : "") +
      "><summary>Other departure times</summary>" +
      '<ul class="opt-list">' +
      options.map((o) => departureOptionHtml(o, weather.suggested_departure)).join("") +
      "</ul>" +
      '<p class="weather-note">Same route, forecast for each start. Suggested only when it is ' +
      "drier or keeps you in daylight; wind and temperature are shown, not weighed.</p>" +
      "</details>"
    );
  }

  /** Short label for the candidate table: "▲ 12" (headwind) / "▼ 8" (tailwind). */
  function windCell(weather) {
    if (!weather || !weather.summary) return { text: "—", kind: "none", title: "No wind forecast" };
    const h = weather.summary.headwind_mean_kmh;
    if (typeof h !== "number" || !Number.isFinite(h)) {
      return { text: "—", kind: "none", title: "No wind forecast" };
    }
    const speed = Math.abs(h).toFixed(0);
    if (Math.abs(h) < 3) return { text: "≈ 0", kind: "calm", title: "Wind mostly across the route" };
    return h > 0
      ? { text: `▲ ${speed}`, kind: "head", title: `${speed} km/h headwind on average` }
      : { text: `▼ ${speed}`, kind: "tail", title: `${speed} km/h tailwind on average` };
  }

  /** Tooltip text for a sample marker on the map. */
  function sampleTooltip(sample) {
    const w = sample.weather;
    const info = conditionInfo(w.condition, w.is_day);
    const bits = [`${clock(sample.time)} · ${info.label}`];
    const t = num(w.temperature_c);
    if (t !== null) bits.push(`${t} °C`);
    const speed = num(w.wind_speed_kmh);
    if (speed !== null && typeof w.wind_from_deg === "number") {
      bits.push(`wind ${speed} km/h from ${compass(w.wind_from_deg)}`);
    }
    const rain = num(w.precipitation_probability);
    if (rain !== null) bits.push(`${rain}% rain`);
    return bits.join(" · ");
  }

  const api = {
    conditionInfo,
    compass,
    circularMean,
    windArrow,
    temperatureRange,
    feelsLike,
    wetStretchText,
    daylightText,
    windVsRoute,
    clock,
    dayAndClock,
    providerCredit,
    weatherCardHtml,
    windCell,
    offsetLabel,
    departureOptionsHtml,
    sampleTooltip,
    escapeHtml,
  };
  root.BikeWeather = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
