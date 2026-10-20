/* The chat panel (pure functions, no DOM access): messages and quick replies as HTML.
 *
 * The server does the thinking (POST /v1/chat, docs/chat.md); this file only turns text into safe
 * markup. Tested under node (tests/test_frontend_chat.py).
 */
(function (root) {
  "use strict";

  const MAX_MESSAGE = 500; // the server's limit

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // The files of a route are API paths the chat mentions: make exactly those clickable.
  const FILE_PATH = /\/v1\/routes\/[0-9a-f]{32}\.(?:gpx|geojson)/g;

  /** The text of a message as HTML: escaped, line breaks kept, route files as download links. */
  function textHtml(text) {
    return escapeHtml(text)
      .replace(FILE_PATH, (path) => `<a href="${path}" download>${path.split("/").pop()}</a>`)
      .replace(/\n/g, "<br>");
  }

  /** One message bubble. `role` is "user" or "bot". */
  function bubbleHtml(role, text) {
    const who = role === "user" ? "user" : "bot";
    return `<div class="chat-msg ${who}"><span class="chat-who">${who === "user" ? "You" : "Bot"}</span><div class="chat-text">${textHtml(text)}</div></div>`;
  }

  /** The quick replies as buttons (the text of a button is what gets sent). */
  function chipsHtml(suggestions) {
    return (suggestions || [])
      .filter((s) => typeof s === "string" && s.trim() && s.length <= MAX_MESSAGE)
      .slice(0, 8)
      .map((s) => `<button type="button" class="chat-chip ghost" data-say="${escapeHtml(s)}">${escapeHtml(s)}</button>`)
      .join("");
  }

  /** Trim a typed message; null when there is nothing to send. */
  function outgoing(text) {
    const trimmed = String(text || "").trim();
    return trimmed ? trimmed.slice(0, MAX_MESSAGE) : null;
  }

  /** The position of the alternative with this rank in the candidates a plan lists (or -1). */
  function candidateIndex(plan, rank) {
    const list = (plan && plan.candidates) || [];
    return list.findIndex((c) => c.rank === rank);
  }

  /** What to show when the chat request itself failed. */
  function failureText(status) {
    if (status === 503) return "The chat is switched off on this server.";
    if (status === 422) return "I could not use that message (too long or empty?).";
    return status ? `The chat failed (HTTP ${status}). Please try again.` : "The chat could not be reached.";
  }

  /* The conversation lives on the server; the browser keeps only its id, to find it again. */
  const SESSION_KEY = "pybikerouter.chat";
  const SESSION_ID = /^[0-9a-f]{32}$/;

  /** The remembered session id, or null (nothing stored, a malformed value, blocked storage). */
  function storedSession(storage) {
    try {
      const id = storage.getItem(SESSION_KEY);
      return typeof id === "string" && SESSION_ID.test(id) ? id : null;
    } catch {
      return null;
    }
  }

  /** Remember the session id (null forgets it); storage that is blocked just forgets. A valid id
   * also goes to the front of this browser's list of conversations (`knownSessions`). */
  function rememberSession(storage, id) {
    try {
      if (id && SESSION_ID.test(id)) {
        storage.setItem(SESSION_KEY, id);
        writeKnown(storage, [id, ...knownSessions(storage).filter((k) => k !== id)]);
      } else storage.removeItem(SESSION_KEY);
    } catch {
      /* private window or blocked storage: the conversation is just not found again */
    }
  }

  /* The conversations this browser has had: only their ids, newest first. The server answers for
   * these ids and lists nothing else, so a session id stays the one handle to a conversation. */
  const KNOWN_KEY = "pybikerouter.chats";
  const MAX_KNOWN = 30; // the server's limit per request

  function knownSessions(storage) {
    try {
      const list = JSON.parse(storage.getItem(KNOWN_KEY));
      if (!Array.isArray(list)) return [];
      const seen = new Set();
      return list.filter((id) => typeof id === "string" && SESSION_ID.test(id) && !seen.has(id) && seen.add(id)).slice(0, MAX_KNOWN);
    } catch {
      return [];
    }
  }

  function writeKnown(storage, ids) {
    try {
      storage.setItem(KNOWN_KEY, JSON.stringify(ids.slice(0, MAX_KNOWN)));
    } catch {
      /* blocked storage: the list is just not kept */
    }
  }

  /** Drop an id from the list (deleted, or the server no longer knows it). */
  function forgetKnown(storage, id) {
    writeKnown(storage, knownSessions(storage).filter((k) => k !== id));
  }

  /** Keep only the ids in `keep` (what the server still knows), in the order given. */
  function keepKnown(storage, keep) {
    writeKnown(storage, keep.filter((id) => typeof id === "string" && SESSION_ID.test(id)));
  }

  /** "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", else the date. */
  function relativeTime(thenSeconds, nowSeconds) {
    if (thenSeconds === null || thenSeconds === undefined) return "";
    const diff = Number(nowSeconds) - Number(thenSeconds);
    if (!Number.isFinite(diff)) return "";
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
    if (diff < 2 * 86400) return "yesterday";
    if (diff < 7 * 86400) return `${Math.floor(diff / 86400)} days ago`;
    return new Date(Number(thenSeconds) * 1000).toISOString().slice(0, 10);
  }

  /** The list of earlier conversations as HTML; `currentId` is marked. Empty without any. */
  function conversationsHtml(summaries, nowSeconds, currentId) {
    return (summaries || [])
      .filter((s) => s && SESSION_ID.test(String(s.session_id)))
      .map((s) => {
        const meta = `${escapeHtml(relativeTime(s.updated, nowSeconds))} &middot; ${Number(s.messages) || 0} messages`;
        const current = s.session_id === currentId;
        return (
          `<li class="chat-earlier-item${current ? " current" : ""}">` +
          `<button type="button" class="chat-earlier-open" data-open="${s.session_id}"${current ? ' aria-current="true"' : ""}>` +
          `<span class="chat-earlier-title">${escapeHtml(s.title || "Conversation")}</span>` +
          `<span class="chat-earlier-meta">${meta}</span></button>` +
          `<button type="button" class="chat-earlier-delete" data-delete="${s.session_id}" title="Delete this conversation" aria-label="Delete this conversation">&times;</button>` +
          `</li>`
        );
      })
      .join("");
  }

  /** The messages of a stored conversation that can be shown (a role and some text each). */
  function historyMessages(history) {
    return ((history && history.messages) || [])
      .filter((m) => m && (m.role === "user" || m.role === "bot") && typeof m.text === "string" && m.text.trim())
      .map((m) => ({ role: m.role, text: m.text }));
  }

  const api = {
    MAX_MESSAGE,
    escapeHtml,
    textHtml,
    bubbleHtml,
    chipsHtml,
    outgoing,
    candidateIndex,
    failureText,
    storedSession,
    rememberSession,
    historyMessages,
    knownSessions,
    forgetKnown,
    keepKnown,
    relativeTime,
    conversationsHtml,
  };
  root.BikeChat = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
