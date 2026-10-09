/* The settings panel that slides out from the right (pure helpers, no DOM access).
 *
 * The panel is hidden until the gear button is pressed; this file only decides what the
 * attributes should say and when a key closes it. Tested under node
 * (tests/test_frontend_settings.py).
 */
(function (root) {
  "use strict";

  /** The attribute values for an open or closed panel (strings, as attributes want them). */
  function view(open) {
    return {
      expanded: String(Boolean(open)),
      hidden: String(!open),
      label: open ? "Close settings" : "Open settings",
    };
  }

  /** Escape closes an open panel; nothing else does (the map stays usable while it is open). */
  function closesOnKey(key, open) {
    return Boolean(open) && (key === "Escape" || key === "Esc");
  }

  /** True when every settings group is hidden, so the panel should say there is nothing yet. */
  function isEmpty(groupHiddenFlags) {
    return groupHiddenFlags.every(Boolean);
  }

  const api = { view, closesOnKey, isEmpty };
  root.BikeSettings = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
