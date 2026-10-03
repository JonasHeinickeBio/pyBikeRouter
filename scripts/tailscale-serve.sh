#!/usr/bin/env bash
# Publish the local bike-router API to your tailnet over HTTPS (docs/mobile.md).
#
#   scripts/tailscale-serve.sh            # start proxying https://<host>.<tailnet>.ts.net:8443
#   scripts/tailscale-serve.sh status     # show what is served
#   scripts/tailscale-serve.sh off        # stop (only an entry proxying to this app's port)
#
# The app itself stays bound to 127.0.0.1; `tailscale serve` is the only thing
# that exposes it, and only to devices on your tailnet (never the public
# internet -- this script does not use `tailscale funnel`). The app has no
# login of its own, so tailnet membership / ACLs are the access control.
#
# Safety: `start` and `off` inspect `tailscale serve status` first and refuse
# to replace or remove an endpoint on the chosen HTTPS port that does not
# proxy to this app (APP_PORT), so another service's serve config is never
# overwritten.
#
# Environment:
#   APP_PORT        local port of the running app          (default 8000);
#                   `off` only removes a serve entry that targets this port
#   TS_HTTPS_PORT   HTTPS port on the tailnet side; Tailscale allows 443,
#                   8443 and 10000. Defaults to 8443 so an existing
#                   `tailscale serve` on 443 is left alone.
set -euo pipefail

APP_PORT="${APP_PORT:-8000}"
TS_HTTPS_PORT="${TS_HTTPS_PORT:-8443}"
ACTION="${1:-start}"

die() { echo "error: $*" >&2; exit 1; }

command -v tailscale >/dev/null 2>&1 || die "tailscale is not installed (https://tailscale.com/download)"

case "$TS_HTTPS_PORT" in
  443|8443|10000) ;;
  *) die "TS_HTTPS_PORT must be 443, 8443 or 10000 (got $TS_HTTPS_PORT)" ;;
esac

# MagicDNS name of this machine, e.g. mybox.tail1234.ts.net (no trailing dot).
dns_name() {
  tailscale status --json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
}

url() {
  local host port_suffix=""
  host="$(dns_name)" || die "could not read this machine's tailnet name; is Tailscale running and logged in?"
  [ "$TS_HTTPS_PORT" = "443" ] || port_suffix=":${TS_HTTPS_PORT}"
  echo "https://${host}${port_suffix}"
}

EXPECTED_TARGET="http://127.0.0.1:${APP_PORT}"

# Classify what `tailscale serve` currently exposes on TS_HTTPS_PORT:
#   absent              nothing is served there
#   ours                a single "/" proxy to EXPECTED_TARGET
#   foreign: <detail>   anything else (another app's proxy, a TCP forward, extra paths)
# `tailscale serve` silently replaces a matching endpoint and `... off` removes
# it, so start and off both refuse to touch anything that is not ours.
serve_state() {
  local json state
  json="$(tailscale serve status --json 2>/dev/null)" \
    || die "could not read 'tailscale serve status'; refusing to change serve configuration blindly"
  state="$(SERVE_JSON="$json" python3 - "$TS_HTTPS_PORT" "$EXPECTED_TARGET" <<'PY'
import json, os, sys

port, expected = sys.argv[1], sys.argv[2]
raw = os.environ.get("SERVE_JSON", "").strip()
cfg = json.loads(raw) if raw else {}
web = {k: v for k, v in cfg.get("Web", {}).items() if k.rsplit(":", 1)[-1] == port}
tcp = cfg.get("TCP", {}).get(port)
if not web and not tcp:
    print("absent")
elif web:
    handlers = [item for entry in web.values() for item in entry.get("Handlers", {}).items()]
    if len(handlers) == 1 and handlers[0][0] == "/" and handlers[0][1].get("Proxy") == expected:
        print("ours")
    else:
        parts = [
            f"{path} -> {h.get('Proxy') or h.get('Path') or h.get('Text') or h}"
            for path, h in handlers
        ]
        print("foreign: " + ", ".join(parts))
else:
    print("foreign: " + json.dumps(tcp))
PY
  )" || die "could not interpret 'tailscale serve status'; refusing to change serve configuration"
  [ -n "$state" ] || die "empty answer from the serve-status check; refusing to change serve configuration"
  echo "$state"
}

refuse_foreign() {
  echo "error: HTTPS port ${TS_HTTPS_PORT} is in use by something else:" >&2
  echo "       ${1#foreign: }" >&2
  echo "       This script only manages a proxy to ${EXPECTED_TARGET}." >&2
  echo "       Pick another port (TS_HTTPS_PORT=8443 or 10000), set APP_PORT to match," >&2
  echo "       or change it yourself with the tailscale CLI." >&2
  exit 1
}

case "$ACTION" in
  start)
    state="$(serve_state)"
    case "$state" in foreign:*) refuse_foreign "$state" ;; esac
    if ! curl -fsS -o /dev/null "http://127.0.0.1:${APP_PORT}/healthz"; then
      echo "warning: nothing answered on http://127.0.0.1:${APP_PORT}/healthz --" >&2
      echo "         start the app first:  bike-router serve start --port ${APP_PORT}" >&2
    fi
    tailscale serve --bg --https="${TS_HTTPS_PORT}" "http://127.0.0.1:${APP_PORT}"
    echo
    echo "Open on your iPhone (Tailscale connected):  $(url)"
    ;;
  status)
    tailscale serve status
    ;;
  off)
    state="$(serve_state)"
    case "$state" in
      foreign:*) refuse_foreign "$state" ;;
      absent) echo "nothing of ours is served on HTTPS port ${TS_HTTPS_PORT}" ;;
      *)
        tailscale serve --https="${TS_HTTPS_PORT}" off
        echo "stopped serving on HTTPS port ${TS_HTTPS_PORT}"
        ;;
    esac
    ;;
  *)
    die "unknown action '$ACTION' (use start, status or off)"
    ;;
esac
