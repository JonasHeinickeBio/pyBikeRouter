# iPhone: the planner as a home-screen app over Tailscale

The web UI is a responsive, installable web app, and
[Tailscale](https://tailscale.com) is how an iPhone reaches a bike-router
running on your own machine -- no cloud hosting, no App Store, nothing exposed
to the public internet. (A native iOS app is out of scope; see issue #6.)

```
iPhone (Tailscale app) --WireGuard--> your machine: tailscale serve (HTTPS) --> 127.0.0.1:8000
```

## Why `tailscale serve` (HTTPS)

Plain `http://<tailscale-ip>:8000` would load, but iOS only enables the
**"use my location"** button and the full-screen home-screen mode on secure
origins. `tailscale serve` gives the app a real HTTPS certificate for your
machine's MagicDNS name and keeps the app bound to `127.0.0.1`, so nothing
else on your LAN can reach it either.

## One-time setup

1. Install Tailscale on the iPhone and the machine running the app, signed in
   to the same tailnet.
2. In the Tailscale admin console enable **MagicDNS** and **HTTPS
   Certificates** (DNS settings).
3. Install and configure the app as usual ([README](../README.md)); an
   `ORS_API_KEY` is needed for routing.

## Run

```bash
bike-router serve start            # binds 127.0.0.1:8000
scripts/tailscale-serve.sh         # in another terminal; prints the URL
```

The script publishes `https://<machine>.<tailnet>.ts.net:8443` (tailnet only)
and prints it. Open that URL in **Safari** on the iPhone, then
**Share -> Add to Home Screen**. It then launches full-screen as
"BikeRouter".

| Command | Effect |
| --- | --- |
| `scripts/tailscale-serve.sh` | start proxying |
| `scripts/tailscale-serve.sh status` | show what `tailscale serve` is exposing |
| `scripts/tailscale-serve.sh off` | stop (removes only an endpoint that proxies to this app) |

`start` and `off` first read `tailscale serve status` and **refuse to replace
or remove** anything on that HTTPS port that is not a proxy to this app
(another service's proxy, a TCP forward, extra paths), or if the status
cannot be read. `off` therefore needs the same `APP_PORT` you started with.

Environment: `APP_PORT` (local port, default `8000`) and `TS_HTTPS_PORT`
(`443`, `8443` or `10000`; default `8443` so an existing `tailscale serve` on
443 is not overwritten).

Using Docker instead: `APP_BIND=127.0.0.1 docker compose -f docker/compose.yaml up`
publishes the container on loopback only (the default, `0.0.0.0`, is open to
your LAN), then run the same script.

## What the phone layout does

- Map on top, form and results scroll underneath; after a plan finishes
  (or needs a clarification) the page scrolls to the result.
- 16px inputs (iOS Safari zooms into smaller ones on focus) and 44px touch
  targets; notch and home-indicator safe areas respected.
- The arrow button next to **Origin** fills it with your current position
  (needs HTTPS and the iOS location permission for Safari/the home-screen
  app). `Pick on map` works with a tap.
- Small landscape phones put the map beside the form; larger ones use the
  desktop layout.

## Security notes

- The app has **no login**. Anyone who can reach your machine on the tailnet
  can plan routes, spending your `ORS_API_KEY` quota, and (with a database
  configured) read recorded history. Restrict it with Tailscale ACLs/grants
  if the tailnet is shared.
- The script never uses `tailscale funnel` (public internet) and a test
  guards against that.
- Location is read in the browser and only becomes the origin
  coordinate in the request; it is not stored by the page. If a database is
  configured, the request (including that coordinate) is recorded in the
  route history like any other.

## Limits

- No offline mode or service worker: routing needs the server and map tiles
  need the network.
- Map tiles come from the public OpenStreetMap tile server straight from the
  phone (subject to its usage policy); the app does not proxy them.
- Not verified on a physical iPhone in this repository's tests: the layout
  was checked at 390x844 in a desktop browser emulation, and the location
  flow against mocked geolocation.
