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

  /** Remember the session id (null forgets it); storage that is blocked just forgets. */
  function rememberSession(storage, id) {
    try {
      if (id && SESSION_ID.test(id)) storage.setItem(SESSION_KEY, id);
      else storage.removeItem(SESSION_KEY);
    } catch {
      /* private window or blocked storage: the conversation is just not found again */
    }
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
  };
  root.BikeChat = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
