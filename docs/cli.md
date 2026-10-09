# Command-line interface

`bike-router` (also `python -m bike_routing_agent.cli`) does what the API and the web form do,
from a terminal. It runs **in-process** with the settings of the environment / `.env` -- no server
has to be running -- and shares code with the API: the point-of-interest, history and readiness
commands call the API's own endpoint functions, so the validation and the errors are the same.

Exit codes: `0` success, `1` the command ran but failed (no route, a service is down, not ready),
`2` a usage error (bad option, rejected request).

| Group | Does |
| --- | --- |
| `route` | plan, show a saved plan again, export any alternative as GPX / GeoJSON |
| `poi` | sights and services along a route or in an area, and what Wikipedia says about one |
| `brouter` | map tiles: which a trip needs, what is on disk, download them |
| `history` | past plans from the PostGIS route history |
| `status` | readiness of every component and the active features |
| `serve`, `docker`, `config`, `providers`, `retention`, `db` | run and operate the deployment |

## `route`

```bash
bike-router route plan --origin 52.26,10.52 --destination 52.49,10.55 --bike-type gravel \
    --format text --output plan.json --gpx best.gpx
bike-router route plan --origin "Füssen" --destination "Oberammergau" --poi-stops 2
bike-router route plan --origin 51.75,-1.25 --destination 51.80,-1.20 --engine ors
bike-router route show plan.json                       # the same summary, later
bike-router route export plan.json --candidate 2 --gpx second.gpx --geojson second.geojson
```

`route plan` takes everything the API request does: `--via` (repeatable), `--loop` /
`--target-distance-km`, surfaces, traffic and ferries, `--departure-time`, plus

| Option | Does |
| --- | --- |
| `--max-alternatives N` | keep at most N distinct alternatives (1-5) |
| `--engine ors\|brouter\|valhalla` | route with this engine for this plan only (repeatable) |
| `--poi-stops N` `--poi-categories` `--poi-corridor-km` `--poi-min-fame` | route past the N best-known sights ([pois.md](pois.md#routing-past-the-most-famous-sights)) |
| `--format json\|text` | machine readable (default) or a readable summary: the route, every alternative with its pros and cons, weather, stops, warnings, files |
| `--gpx FILE` `--geojson FILE` `--candidate RANK` | write the best route, or the alternative of that rank, as GPX / GeoJSON |
| `--output FILE` | also save the JSON (with every alternative and its geometry) for `route show` / `route export` / `poi along` |

The request is checked with the API's own rules before anything is routed (stops with a loop,
too many via points, unknown sight kinds ... are usage errors, exit `2`). The JSON output has the
same fields as the API response: `route`, `candidates` (every ranked alternative), `poi_stops`,
`poi_stops_status`, `weather_status`, `errors`, `artifacts`, `plan_id`.

## `poi`

```bash
bike-router poi categories
bike-router poi along --route plan.json --category historic,museum --buffer-m 1500
bike-router poi along --from 52.26,10.52 --to 52.49,10.55
bike-router poi bbox --bbox 10.50,52.25,10.56,52.29 --category attraction --limit 10
bike-router poi info --wikidata Q632379 --lang de
```

`along --route` reads a plan saved with `route plan --output` (`--candidate RANK` picks an
alternative) or a GeoJSON file with a line. Results are ranked by fame (the number of Wikipedia
languages describing the place; `-` = not measured, never "obscure") and show the distance off the
route and the position along it. `--format json` gives the API's response.

## `brouter`

```bash
bike-router brouter needed --origin 51.75,-1.25 --destination 52.26,10.52 --dir docker/brouter/segments
bike-router brouter info W5_N50 --dir docker/brouter/segments
bike-router brouter download W5_N50 --dir docker/brouter/segments        # asks first
```

The terminal counterpart of the web form's *Map data missing* card
([providers.md](providers.md#brouter-map-coverage)). `needed` lists the 5-degree tiles a trip
touches and whether each is on disk (coordinates only, no network). `info` shows presence, the
size at the source and the free disk space. `download` shows the size and **asks** before
fetching (`--yes` skips the question; without a terminal it refuses unless `--yes` is given),
shows progress, and writes each tile through a `.part` file and an atomic rename; the same limits
as the server apply (`BROUTER_SEGMENTS_MAX_MB`, free disk space, official source only). The folder
is `--dir` or `BROUTER_SEGMENTS_DIR`.

## `history`

```bash
bike-router history list --status ready --bike-type gravel --since 2026-10-01 --limit 10
bike-router history show <plan_id>
bike-router history stats
```

Needs `DATABASE_URL` ([persistence.md](persistence.md)); the filters are those of
`/v1/history/plans`.

## `status`

```bash
bike-router status show            # exit 0 when ready, 1 when not
bike-router status show --format json
```

Probes every component (routing engines, geocoder, artifact store, weather, database, cache) like
`/readyz` and lists the active features like `/v1/capabilities`.

## Not covered

- It always runs in-process; there is no `--server URL` mode that talks to a running API.
- `brouter needed` takes coordinates, not place names.
- Shell completion is not provided.
