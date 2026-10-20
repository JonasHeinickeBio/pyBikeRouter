/* Are the opening hours of a sight compatible with when you pass it? (pure functions, no DOM)
 *
 * OSM writes opening hours in a small language ("Mo-Fr 09:00-18:00; Sa 10:00-14:00; PH off").
 * This file reads the common part of it and says "open", "closed" or nothing at all for a time of
 * day at the sight. It never guesses: whatever it does not understand (months, weeks, sunrise,
 * "+" open ends, comments, extra rules after a comma) makes the answer "unknown", and the raw text
 * stays the thing shown. Public and school holidays are not known, so a text that mentions them
 * is answered for an ordinary day and says so.
 *
 * The time of arrival is the departure plus the share of the ride before the sight, read in the
 * zone the server named for the route. Tested under node (tests/test_frontend_hours.py).
 */
(function (root) {
  "use strict";

  const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
  const DAY = "(?:Mo|Tu|We|Th|Fr|Sa|Su|PH|SH)";
  const DAY_LIST = `${DAY}(?:-${DAY})?(?:,${DAY}(?:-${DAY})?)*`;
  const TIME = "\\d{1,2}:\\d{2}";
  const TIME_LIST = `${TIME}-${TIME}(?:\\s*,\\s*${TIME}-${TIME})*`;
  const RULE = new RegExp(`^(?:(${DAY_LIST})\\s+)?(?:(${TIME_LIST})(?:\\s+(open))?|(off|closed|open))$`);
  const DAY_MINUTES = 1440;

  function minutes(clock, isEnd) {
    const [h, m] = clock.split(":").map(Number);
    if (m > 59) return null;
    if (h === 24) return isEnd && m === 0 ? DAY_MINUTES : null;
    return h > 23 ? null : h * 60 + m;
  }

  /** Days (0 = Monday) of "Mo-Fr,Su" and whether a holiday selector was in it; null if invalid. */
  function parseDays(text) {
    const days = new Set();
    let holidays = false;
    for (const item of text.split(",")) {
      const [from, to] = item.split("-");
      if (["PH", "SH"].includes(from) || ["PH", "SH"].includes(to)) {
        if (to !== undefined) return null; // "Mo-PH" means nothing
        holidays = true;
        continue;
      }
      const start = DAYS.indexOf(from);
      const end = to === undefined ? start : DAYS.indexOf(to);
      for (let d = start; ; d = (d + 1) % 7) {
        days.add(d);
        if (d === end) break;
      }
    }
    return { days, holidays };
  }

  /** [[start, end], ...] in minutes of the day; an end past 1440 runs into the next morning. */
  function parseTimes(text) {
    const spans = [];
    for (const part of text.split(",")) {
      const [from, to] = part.trim().split("-");
      const start = minutes(from, false);
      let end = minutes(to, true);
      if (start === null || end === null || start === end) return null;
      if (end < start) end += DAY_MINUTES;
      spans.push([start, end]);
    }
    return spans;
  }

  /**
   * The hours as {rules, holidays}, or null when the text is not (entirely) understood.
   * A rule is {days: Set of 0..6, spans: [[from, to]]}; no spans = closed on those days.
   */
  function parse(text) {
    if (typeof text !== "string") return null;
    const source = text.trim();
    if (!source || source.length > 300) return null;
    if (source === "24/7") {
      return { rules: [{ days: new Set([0, 1, 2, 3, 4, 5, 6]), spans: [[0, DAY_MINUTES]] }], holidays: false };
    }
    const rules = [];
    let holidays = false;
    for (const raw of source.split(";")) {
      const part = raw.trim();
      if (!part) continue;
      const match = RULE.exec(part);
      if (!match) return null;
      const [, dayText, timeText, , word] = match;
      let days = new Set([0, 1, 2, 3, 4, 5, 6]);
      if (dayText) {
        const parsed = parseDays(dayText);
        if (!parsed) return null;
        days = parsed.days;
        holidays = holidays || parsed.holidays;
      }
      let spans;
      if (timeText) {
        spans = parseTimes(timeText);
        if (!spans) return null;
      } else {
        spans = word === "open" ? [[0, DAY_MINUTES]] : [];
      }
      rules.push({ days, spans });
    }
    return rules.length ? { rules, holidays } : null;
  }

  /** What a day's rules finally say: a later rule that names the day replaces the earlier ones. */
  function spansOf(parsed, day) {
    let spans = [];
    for (const rule of parsed.rules) if (rule.days.has(day)) spans = rule.spans;
    return spans;
  }

  /** When an open stretch ending at `to` really ends: a stretch that runs to midnight and starts
   * again at 00:00 goes on (24/7 never ends: null). Minutes from the start of `day`. */
  function closingAfter(parsed, day, to) {
    let end = to;
    for (let i = 1; i <= 7 && end === DAY_MINUTES; i++) {
      const next = spansOf(parsed, (day + i) % 7).find(([from]) => from === 0);
      if (!next) return end;
      end = next[1];
      if (end === DAY_MINUTES && i === 7) return null;
      if (end !== DAY_MINUTES) return end;
    }
    return end === DAY_MINUTES ? null : end;
  }

  /**
   * Open or closed at `minute` of `day` (0 = Monday), from text in the OSM language.
   * {state: "open"|"closed"|"unknown", until, opensAt, holidays}: `until` / `opensAt` are minutes
   * of the day (`until` may pass midnight), only when the text says so.
   */
  function evaluate(text, day, minute) {
    const parsed = parse(text);
    if (!parsed) return { state: "unknown" };
    const holidays = parsed.holidays;
    const today = spansOf(parsed, day);
    for (const [from, to] of today) {
      if (minute >= from && minute < to) return { state: "open", until: closingAfter(parsed, day, to), holidays };
    }
    // Yesterday's late hours that run past midnight ("Fr 20:00-02:00").
    for (const [, to] of spansOf(parsed, (day + 6) % 7)) {
      if (to > DAY_MINUTES && minute < to - DAY_MINUTES) return { state: "open", until: to - DAY_MINUTES, holidays };
    }
    const next = today.map(([from]) => from).filter((from) => from > minute).sort((a, b) => a - b)[0];
    return { state: "closed", opensAt: next === undefined ? null : next, holidays };
  }

  /** {day: 0..6 (Monday = 0), minute} of an instant (ms) in an IANA zone, or null if unknown. */
  function localParts(ms, timeZone) {
    if (!Number.isFinite(ms) || typeof timeZone !== "string") return null;
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone,
        weekday: "short",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).formatToParts(new Date(ms));
      const get = (type) => (parts.find((p) => p.type === type) || {}).value;
      const day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(get("weekday"));
      const hour = Number(get("hour"));
      const minute = Number(get("minute"));
      if (day < 0 || !Number.isFinite(hour) || !Number.isFinite(minute)) return null;
      return { day, minute: (hour % 24) * 60 + minute };
    } catch {
      return null; // a zone this browser does not know
    }
  }

  /** The instant (ms) at which the ride passes a POI: departure + its share of the duration. */
  function passingMs(poi, ride) {
    if (!poi || !ride || poi.along_route_km == null || !(ride.distance_m > 0)) return null;
    const start = Date.parse(ride.departure);
    const end = Date.parse(ride.arrival);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
    const share = Math.min(1, Math.max(0, (poi.along_route_km * 1000) / ride.distance_m));
    return start + share * (end - start);
  }

  function clock(minute) {
    const wrapped = minute % DAY_MINUTES;
    const h = Math.floor(wrapped / 60);
    return `${String(h).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`;
  }

  /**
   * The line for a POI popup: {state, text} or null when there is nothing to say (no hours, hours
   * not understood, no departure / zone). `ride` = {departure, arrival, distance_m, timezone}.
   */
  function check(poi, ride) {
    if (!poi || !poi.opening_hours || !ride || !ride.timezone) return null;
    const ms = passingMs(poi, ride);
    const local = ms === null ? null : localParts(ms, ride.timezone);
    if (!local) return null;
    const verdict = evaluate(poi.opening_hours, local.day, local.minute);
    if (verdict.state === "unknown") return null;
    const when = `about ${clock(local.minute)}`;
    const tail = verdict.holidays ? " Public holidays are not checked." : "";
    if (verdict.state === "open") {
      const until =
        verdict.until === null ? ", around the clock" : verdict.until === DAY_MINUTES ? " until midnight" : ` until ${clock(verdict.until)}`;
      return { state: "open", text: `Open when you pass (${when})${until}.${tail}` };
    }
    const opens = verdict.opensAt === null ? "" : `, opens ${clock(verdict.opensAt)}`;
    return { state: "closed", text: `Closed when you pass (${when})${opens}.${tail}` };
  }

  const api = { parse, evaluate, localParts, passingMs, check, clock };
  root.BikeHours = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
