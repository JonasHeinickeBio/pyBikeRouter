/* Are the opening hours of a sight compatible with when you pass it? (pure functions, no DOM)
 *
 * OSM writes opening hours in a small language ("Mo-Fr 09:00-18:00; Sa 10:00-14:00; PH off").
 * This file reads the common part of it and says "open", "closed" or nothing at all for a time of
 * day at the sight. It never guesses: whatever it does not understand (weeks, sunrise, days of the
 * month, comments, "20:00-02:00+") makes the answer "unknown", and the raw text stays the thing
 * shown. Understood: spaces anywhere ("Mo, Th 10:00 - 18:00"), month ranges ("Apr-Oct"), open ends
 * ("17:00+", open from then, no closing time given) and rules added after a comma
 * ("Mo-Fr 10:00-18:00, Sa 10:00-14:00"). Public and school holidays are not known, so a text that mentions them
 * is answered for an ordinary day and says so.
 *
 * The time of arrival is the departure plus the share of the ride before the sight, read in the
 * zone the server named for the route. Tested under node (tests/test_frontend_hours.py).
 */
(function (root) {
  "use strict";

  const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAY = "(?:Mo|Tu|We|Th|Fr|Sa|Su|PH|SH)";
  const DAY_LIST = `${DAY}(?:-${DAY})?(?:,${DAY}(?:-${DAY})?)*`;
  const MONTH = `(?:${MONTHS.join("|")})`;
  const MONTH_LIST = `${MONTH}(?:-${MONTH})?(?:,${MONTH}(?:-${MONTH})?)*`;
  const TIME = "\\d{1,2}:\\d{2}";
  // "10:00-18:00", "10:00-23:00+" (possibly later) or "17:00+" (open from then, no closing time).
  const TIME_ITEM = `${TIME}(?:-${TIME}\\+?|\\+)`;
  const TIME_LIST = `${TIME_ITEM}(?:,${TIME_ITEM})*`;
  // [months] [days] then: times or off / closed / open.
  const RULE = new RegExp(
    `^(?:(${MONTH_LIST})\\s+)?(?:(${DAY_LIST})\\s+)?(?:(${TIME_LIST})(?:\\s+(open))?|(off|closed|open))$`,
  );
  const NORMAL = 0;
  const OPEN_END = 1; // "17:00+": no closing time given
  const PLUS = 2; // "10:00-23:00+": until 23:00, perhaps later
  const DAY_MINUTES = 1440;
  const NEW_RULE = "\u0001"; // marks "..., Sa 10:00-12:00": a rule that adds to the one before it

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

  /** Months (1 = January) of "Apr-Oct" / "Nov-Feb" (wraps the year) / "Jan,Dec". */
  function parseMonths(text) {
    const months = new Set();
    for (const item of text.split(",")) {
      const [from, to] = item.split("-");
      const start = MONTHS.indexOf(from);
      const end = to === undefined ? start : MONTHS.indexOf(to);
      for (let m = start; ; m = (m + 1) % 12) {
        months.add(m + 1);
        if (m === end) break;
      }
    }
    return months;
  }

  /** [[start, end, kind], ...] in minutes of the day; an end past 1440 runs into the next morning. */
  function parseTimes(text) {
    const spans = [];
    for (const part of text.split(",")) {
      if (part.endsWith("+") && !part.includes("-")) {
        const from = minutes(part.slice(0, -1), false);
        if (from === null) return null;
        spans.push([from, DAY_MINUTES, OPEN_END]);
        continue;
      }
      const plus = part.endsWith("+");
      const [from, to] = (plus ? part.slice(0, -1) : part).split("-");
      const start = minutes(from, false);
      let end = minutes(to, true);
      if (start === null || end === null || start === end) return null;
      if (end < start) end += DAY_MINUTES;
      spans.push([start, end, plus ? PLUS : NORMAL]);
    }
    return spans;
  }

  /** One rule's text -> {months, days, spans, holidays} or null. `spans` entries are [from, to, openEnd]. */
  function parseRule(part) {
    const match = RULE.exec(part);
    if (!match) return null;
    const [, monthText, dayText, timeText, , word] = match;
    let days = new Set([0, 1, 2, 3, 4, 5, 6]);
    let holidays = false;
    if (dayText) {
      const parsed = parseDays(dayText);
      if (!parsed) return null;
      days = parsed.days;
      holidays = parsed.holidays;
    }
    let spans;
    if (timeText) {
      spans = parseTimes(timeText);
      if (!spans) return null;
    } else {
      spans = word === "open" ? [[0, DAY_MINUTES, NORMAL]] : [];
    }
    return { months: monthText ? parseMonths(monthText) : null, days, spans, holidays };
  }

  /** The text with spaces made uniform ("Mo, Th", "10:00 - 18:00") and additional rules marked. */
  function normalise(text) {
    const dayStart = "(?=(?:Mo|Tu|We|Th|Fr|Sa|Su|PH|SH)\\b)";
    return text
      .trim()
      .replace(/\s+/g, " ")
      .replace(/([A-Za-z]):(?= )/g, "$1") // "Apr-Oct: 10:00-21:00"
      .replace(/\s*-\s*/g, "-")
      .replace(/\s*,\s*/g, ",")
      .replace(new RegExp(`([\\d+]|off|closed|open),${dayStart}`, "g"), `$1${NEW_RULE}`);
  }

  /**
   * The hours as {rules, holidays, usesMonths}, or null when the text is not (entirely) understood.
   * A rule is {months: Set 1..12 | null, days: Set 0..6, spans: [[from, to, openEnd]], additive};
   * no spans = closed on those days. An *additive* rule (after a comma) adds to what came before.
   */
  function parse(text) {
    if (typeof text !== "string") return null;
    if (!text.trim() || text.length > 300) return null;
    const source = normalise(text);
    if (source === "24/7") {
      return { rules: [{ months: null, days: new Set([0, 1, 2, 3, 4, 5, 6]), spans: [[0, DAY_MINUTES, NORMAL]], additive: false }], holidays: false, usesMonths: false };
    }
    const rules = [];
    let holidays = false;
    for (const raw of source.split(";")) {
      const part = raw.trim();
      if (!part) continue;
      const pieces = part.split(NEW_RULE);
      for (let i = 0; i < pieces.length; i++) {
        const rule = parseRule(pieces[i].trim());
        if (!rule) return null;
        holidays = holidays || rule.holidays;
        rules.push({ ...rule, additive: i > 0 });
      }
    }
    return rules.length ? { rules, holidays, usesMonths: rules.some((r) => r.months) } : null;
  }

  /**
   * What a day's rules finally say: a later rule that names the day replaces the earlier ones, an
   * additive one (after a comma) adds to them. A rule with months only counts in those months.
   */
  function spansOf(parsed, day, month) {
    let spans = [];
    for (const rule of parsed.rules) {
      if (!rule.days.has(day) || (rule.months && !rule.months.has(month))) continue;
      spans = rule.additive ? spans.concat(rule.spans) : rule.spans;
    }
    return spans;
  }

  /** When an open stretch ending at `to` really ends: a stretch that runs to midnight and starts
   * again at 00:00 goes on (24/7 never ends: null). Minutes from the start of `day`. */
  function closingAfter(parsed, day, month, to) {
    let end = to;
    for (let i = 1; i <= 7 && end === DAY_MINUTES; i++) {
      const next = spansOf(parsed, (day + i) % 7, month).find(([from]) => from === 0);
      if (!next) return end;
      end = next[1];
      if (end === DAY_MINUTES && i === 7) return null;
      if (end !== DAY_MINUTES) return end;
    }
    return end === DAY_MINUTES ? null : end;
  }

  /**
   * Open or closed at `minute` of `day` (0 = Monday) in `month` (1 = January), from text in the OSM
   * language. {state: "open"|"closed"|"unknown", until, openEnd, opensAt, holidays}: `until` /
   * `opensAt` are minutes of the day (`until` may pass midnight), only when the text says so;
   * `openEnd`: the text gives an opening time and no closing time ("17:00+"). A text with months
   * is "unknown" without a month.
   */
  function evaluate(text, day, minute, month) {
    const parsed = parse(text);
    if (!parsed || (parsed.usesMonths && !month)) return { state: "unknown" };
    const holidays = parsed.holidays;
    const today = spansOf(parsed, day, month);
    for (const [from, to, kind] of today) {
      if (minute >= from && minute < to) {
        if (kind === OPEN_END) return { state: "open", until: null, openEnd: true, holidays };
        if (kind === PLUS) return { state: "open", until: to, atLeast: true, openEnd: false, holidays };
        return { state: "open", until: closingAfter(parsed, day, month, to), openEnd: false, holidays };
      }
    }
    // After a "10:00-23:00+" the place may well be open, and the text does not say: no verdict.
    if (today.some(([from, to, kind]) => kind === PLUS && minute >= to && minute >= from)) return { state: "unknown" };
    // Yesterday's late hours that run past midnight ("Fr 20:00-02:00").
    for (const [, to] of spansOf(parsed, (day + 6) % 7, month)) {
      if (to > DAY_MINUTES && minute < to - DAY_MINUTES) return { state: "open", until: to - DAY_MINUTES, openEnd: false, holidays };
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
        month: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).formatToParts(new Date(ms));
      const get = (type) => (parts.find((p) => p.type === type) || {}).value;
      const day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(get("weekday"));
      const hour = Number(get("hour"));
      const minute = Number(get("minute"));
      const month = Number(get("month"));
      if (day < 0 || !Number.isFinite(hour) || !Number.isFinite(minute) || !(month >= 1 && month <= 12)) return null;
      return { day, minute: (hour % 24) * 60 + minute, month };
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
    const verdict = evaluate(poi.opening_hours, local.day, local.minute, local.month);
    if (verdict.state === "unknown") return null;
    const when = `about ${clock(local.minute)}`;
    const tail = verdict.holidays ? " Public holidays are not checked." : "";
    if (verdict.state === "open") {
      const until = verdict.openEnd
        ? ", no closing time given"
        : verdict.until === null
          ? ", around the clock"
          : verdict.until === DAY_MINUTES
            ? " until midnight"
            : ` until ${verdict.atLeast ? "at least " : ""}${clock(verdict.until)}`;
      return { state: "open", text: `Open when you pass (${when})${until}.${tail}` };
    }
    const opens = verdict.opensAt === null ? "" : `, opens ${clock(verdict.opensAt)}`;
    return { state: "closed", text: `Closed when you pass (${when})${opens}.${tail}` };
  }

  const api = { parse, evaluate, localParts, passingMs, check, clock };
  root.BikeHours = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
