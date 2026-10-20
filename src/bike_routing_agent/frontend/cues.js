/* Turn-by-turn directions for the route shown (pure functions, no DOM access).
 *
 * The server sends `cues` with a route (BRouter's own turn instructions): what to do and where
 * along the route. Street names are not part of them -- BRouter carries none -- so a step says
 * "Turn left", never "onto Hauptstrasse". This file words the steps, adds the distance to the next
 * one, and writes the same steps as a plain-text cue sheet to print or send. Tested under node
 * (tests/test_frontend_cues.py).
 */
(function (root) {
  "use strict";

  const GLYPHS = {
    left: "↰",
    right: "↱",
    slight_left: "↖",
    slight_right: "↗",
    sharp_left: "↙",
    sharp_right: "↘",
    keep_left: "↖",
    keep_right: "↗",
    uturn: "↶",
    roundabout: "⟳",
  };
  const START_GLYPH = "●";
  const FINISH_GLYPH = "⚑";
  const ORDINALS = ["", "1st", "2nd", "3rd"];

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function ordinal(n) {
    return n >= 1 && n <= 3 ? ORDINALS[n] : `${n}th`;
  }

  /** "Turn left", "Keep right", "At the roundabout take the 2nd exit", ... */
  function text(cue) {
    switch (cue && cue.kind) {
      case "left":
        return "Turn left";
      case "right":
        return "Turn right";
      case "slight_left":
        return "Turn slightly left";
      case "slight_right":
        return "Turn slightly right";
      case "sharp_left":
        return "Turn sharp left";
      case "sharp_right":
        return "Turn sharp right";
      case "keep_left":
        return "Keep left";
      case "keep_right":
        return "Keep right";
      case "uturn":
        return "Make a U-turn";
      case "roundabout":
        return cue.exit ? `At the roundabout take the ${ordinal(cue.exit)} exit` : "Go through the roundabout";
      default:
        return "";
    }
  }

  function glyph(cue) {
    return (cue && GLYPHS[cue.kind]) || "";
  }

  /** "350 m" under a kilometre (to 10 m from 100 m up), else "1.2 km". */
  function distanceText(meters) {
    if (!Number.isFinite(meters) || meters < 0) return "";
    if (meters < 100) return `${Math.round(meters)} m`;
    if (meters < 1000) return `${Math.round(meters / 10) * 10} m`;
    return `${(meters / 1000).toFixed(1)} km`;
  }

  /**
   * The rows of the list: a start, one per usable cue, a finish. Each row has `at` (distance from
   * the start, as text), `text`, `glyph`, `legM` (to the next row) and `leg` (as text); a cue row
   * also has `cue`. Cues out of order or past the end of the route are dropped, not trusted.
   */
  function steps(cues, totalM) {
    const total = Number.isFinite(totalM) && totalM > 0 ? totalM : null;
    const usable = [];
    let last = -1;
    for (const cue of Array.isArray(cues) ? cues : []) {
      if (!cue || !text(cue) || !Number.isFinite(cue.distance_m) || cue.distance_m < last) continue;
      if (total !== null && cue.distance_m > total + 50) continue; // measured on the shape, not the engine
      usable.push(cue);
      last = cue.distance_m;
    }
    if (!usable.length) return [];
    const end = total !== null ? Math.max(total, last) : last;
    const rows = [{ at: distanceText(0), text: "Start", glyph: START_GLYPH, distanceM: 0, cue: null }];
    for (const cue of usable) {
      rows.push({ at: distanceText(cue.distance_m), text: text(cue), glyph: glyph(cue), distanceM: cue.distance_m, cue });
    }
    rows.push({ at: distanceText(end), text: "Arrive", glyph: FINISH_GLYPH, distanceM: end, cue: null });
    rows.forEach((row, i) => {
      const next = rows[i + 1];
      row.legM = next ? Math.max(0, next.distanceM - row.distanceM) : null;
      row.leg = row.legM === null ? "" : distanceText(row.legM);
    });
    return rows;
  }

  /** The summary line: "47 turns" / "1 turn", or "" without any. */
  function summary(rows) {
    const turns = (rows || []).filter((r) => r.cue).length;
    return turns ? `${turns} ${turns === 1 ? "turn" : "turns"}` : "";
  }

  /** The list as HTML: one button per step so it works by keyboard and touch. */
  function listHtml(rows) {
    const items = (rows || []).map((row, i) => {
      const leg = row.leg ? `<span class="cue-leg">then ${escapeHtml(row.leg)}</span>` : "";
      return (
        `<li><button type="button" class="cue-row" data-cue="${i}">` +
        `<span class="cue-glyph" aria-hidden="true">${escapeHtml(row.glyph)}</span>` +
        `<span class="cue-at">${escapeHtml(row.at)}</span>` +
        `<span class="cue-text">${escapeHtml(row.text)}</span>${leg}</button></li>`
      );
    });
    return `<ol class="cue-list">${items.join("")}</ol>`;
  }

  /** The same steps as a plain-text cue sheet. */
  function sheetText(rows, title) {
    const lines = [title || "Cue sheet", ""];
    for (const row of rows || []) {
      const leg = row.leg ? `  (then ${row.leg})` : "";
      lines.push(`${row.at.padStart(8)}  ${row.text}${leg}`);
    }
    lines.push("", "Turn instructions are the routing engine's. They carry no street names.");
    return lines.join("\n") + "\n";
  }

  const api = { text, glyph, distanceText, steps, summary, listHtml, sheetText };
  root.BikeCues = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
