# Local BRouter service

BRouter RouteServer for development and the `ROUTING_PROVIDER=brouter`
mode (issue #1). Started from the repository root with:

```bash
docker compose -f docker/compose.yaml --profile brouter up
```

It listens on `http://127.0.0.1:17777` (override with `BROUTER_LOCAL_PORT`)
and serves the same `GET /brouter?lonlats=...&profile=...&format=geojson`
API the `BRouterAdapter` speaks.

## Segments (required, not in git)

BRouter routes from pre-processed OSM network files (`.rd5`, one per 5°x5°
tile). Download the tiles you need from <https://brouter.de/brouter/segments4/>
into `segments/`, then restart the container, e.g. for the Braunschweig
test region (and all of northern Germany):

```bash
curl -o segments/E10_N50.rd5 https://brouter.de/brouter/segments4/E10_N50.rd5   # ~125 MB
```

## lookups.dat (version-matched, in git)

Segments and `lookups.dat` must share a data format version. The container
image ships an old `lookups.dat` (v10), which makes it reject current
downloads with `lookup version mismatch (old rd5?) lookups.dat=10
E10_N50.rd5=11`. We therefore pin a copy of BRouter's current
`misc/profiles2/lookups.dat` (v11) in this folder and mount it over the
image's copy; if you add segments later and hit that error, refresh the pin
from <https://github.com/abrensch/brouter/blob/master/misc/profiles2/lookups.dat>.

## Profiles

`profiles/` is mounted read-only as `/customprofiles`, so request
`profile=custom_<name>` resolves to `profiles/<name>.brf` from this
repository:

| app request (`profile=`) | file | base |
| --- | --- | --- |
| `custom_gravel-v1` | `profiles/gravel-v1.brf` | stock `gravel.brf` (pinned copy) |
| `custom_touring-v1` | `profiles/touring-v1.brf` | stock `trekking.brf`, steps+ferries disallowed |

Stock profiles shipped inside the image (`fastbike`, `mtb`, `trekking`) are
used for the remaining bike types; the mapping lives in
`config.BROUTER_PROFILE_MAP`. Profile files are versioned: never edit one
in place, add `gravel-v2.brf` and update the map instead (exports must stay
reproducible). Files are GPLv3 derivatives of BRouter's `misc/profiles2`.

## Verification

```bash
curl "http://127.0.0.1:17777/robots.txt"   # liveness -> 200
curl "http://127.0.0.1:17777/brouter?lonlats=10.5267132,52.2689081|10.5450128,52.2201356&profile=custom_touring-v1&format=geojson" | head -c 300
pytest -m live tests/live/test_live_brouter.py   # same, via the adapter
```
