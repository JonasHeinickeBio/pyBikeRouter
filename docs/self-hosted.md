# Self-hosted routing and geocoding

The planner talks to two public services by default: openrouteservice
(rate-limited, needs an API key) and Nominatim (1 request/second policy). The
`self-hosted` compose profile replaces both with local services built from
**one regional OSM extract**, so there is no public rate limit and no key:

| Service | Image | Role |
| --- | --- | --- |
| `ors-self-hosted` | `openrouteservice/openrouteservice:v10.0.1` | routing, bicycle profiles only (`cycling-regular`, `-road`, `-mountain`, `-electric`) |
| `nominatim-self-hosted` | `mediagis/nominatim:5.3` | geocoding |

> **openrouteservice does not include a geocoder.** Its documentation says the
> geocoder endpoint "is not part of openrouteservice, but of our public API.
> It is not available when running an own instance of openrouteservice."
> That is why Nominatim runs next to it, and why `GEOCODER_PROVIDER=pelias`
> does *not* work against this stack ([geocoding.md](geocoding.md)).

## Quick start

```bash
scripts/self-hosted-bootstrap.sh
```

It downloads the extract to `docker/self-hosted/data/region.osm.pbf`
(verifying the MD5 Geofabrik publishes next to it), starts the two services
and waits until both answer. Then point the API at them (the script prints
these lines):

```bash
ORS_BASE_URL=http://127.0.0.1:8080/ors
ORS_API_KEY=                 # may stay empty
GEOCODER_PROVIDER=nominatim
GEOCODER_BASE_URL=http://127.0.0.1:8081
GEOCODER_MIN_CONFIDENCE=0
GEOCODER_AMBIGUITY_MARGIN=0
```

Check on it any time with `scripts/self-hosted-bootstrap.sh status`, and with
the API's [`/readyz`](api.md#get-readyz) once it runs (ORS reports `ok`
here, not `unknown`: a self-hosted ORS has a health endpoint).

Both services bind **loopback only** (`127.0.0.1`); neither has authentication.

## What to expect (measured)

On the default extract (Bremen, 21 MB) on a 16-core workstation:

| Step | Time / size |
| --- | --- |
| download Bremen extract | ~21 MB |
| pull images | ORS ~0.5 GB, Nominatim ~1.1 GB |
| ORS first start (graphs for 4 profiles, `XMX=2g`) | about 1-2 minutes |
| Nominatim first start (import + indexing, `THREADS=4`) | about 11 minutes |
| later starts | seconds (graphs and database live in named volumes) |

These are one machine's numbers for a *city-state*; a federal state or country
takes proportionally (and, for the import, super-linearly) longer and needs
much more memory. The compose file caps ORS at 6 GB / heap `ORS_XMX` (default
`4g`) and Nominatim at 4 GB; raise `ORS_XMX`, the container limits and
`NOMINATIM_THREADS` for bigger extracts, following the sizing guidance in the
[ORS](https://giscience.github.io/openrouteservice/run-instance/) and
[Nominatim](https://nominatim.org/release-docs/latest/admin/Import/)
documentation (rules of thumb there depend on the version).

## Choosing another region

```bash
EXTRACT_URL=https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf \
  scripts/self-hosted-bootstrap.sh --force
```

Graphs and the geocoding database belong to the extract they were built from,
so after switching regions remove them and let both rebuild (this deletes only
the stack's own volumes):

```bash
docker compose -f docker/compose.yaml --profile self-hosted rm -sf ors-self-hosted nominatim-self-hosted
docker volume rm pybikerouter-ors-graphs pybikerouter-ors-elevation pybikerouter-nominatim-data
```

Extract-backed ORS graphs are also tied to the ORS version: when bumping the
pinned image tag, remove `pybikerouter-ors-graphs` the same way.

## Configuration notes

- **Profiles.** Only the four bicycle profiles the planner maps bike types
  onto (`config.ORS_PROFILE_MAP`) are enabled; each additional profile costs
  graph-build time and memory. Settings are passed as ORS environment
  variables (`ors.engine.profiles.<name>.enabled`), so no config file needs
  mounting.
- **Elevation.** ORS downloads SRTM tiles on the first graph build (needs
  internet access from the container once) into the `ors-elevation` volume;
  ascent/descent come from them.
- **Ports.** `ORS_LOCAL_PORT` (8080) and `NOMINATIM_LOCAL_PORT` (8081) are the
  host ports; the API container's own port 8000 is unaffected. The script starts
  only these two services, never the `api` service.
- **Other variables.** `ORS_XMS`/`ORS_XMX`, `NOMINATIM_THREADS`,
  `NOMINATIM_DB_PASSWORD` (internal to the Nominatim container, the database
  port is not published).

### Geocoder confidence

The planner asks for clarification when the best geocoding result's
confidence is below `GEOCODER_MIN_CONFIDENCE` or too close to the runner-up
(`GEOCODER_AMBIGUITY_MARGIN`); for Nominatim, confidence is the result's
`importance` ([geocoding.md](geocoding.md)). A fresh local Nominatim has **no
Wikipedia importance ranking**, so almost every result has importance ~0
(`0.00001`-`0.08` in the test import) and the planner would ask to clarify
nearly every place. Two ways out:

1. *(default here)* `GEOCODER_MIN_CONFIDENCE=0` and `GEOCODER_AMBIGUITY_MARGIN=0`:
   the planner accepts Nominatim's own top result and never asks. The trade-off
   is losing clarification dialogues for genuinely ambiguous names.
2. Import the ranking: `NOMINATIM_IMPORT_WIKIPEDIA=true` makes the image fetch
   the Wikipedia importance data, so confidences resemble the public instance
   and the default thresholds make sense again. This option comes from the
   image and was **not exercised** here; expect a bigger download and a longer
   import.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Every route has `ascent_m` 0 | The elevation tile download failed during the first graph build (seen once). Remove `pybikerouter-ors-graphs` and `pybikerouter-ors-elevation` (command above) and start again. `tests/live/test_live_self_hosted.py` checks for this. |
| Container shows `unhealthy` for minutes after start | Normal during the first build/import; the health checks allow ~15 minutes after a 5-minute grace period. Follow `docker compose -f docker/compose.yaml --profile self-hosted logs -f`. |
| `Bind ... port is already allocated` | Another service uses 8080/8081: set `ORS_LOCAL_PORT` / `NOMINATIM_LOCAL_PORT`. |
| ORS exits during graph build | Out of memory: raise `ORS_XMX` and the 6 GB container limit, or use a smaller extract. |
| Planner answers `awaiting_clarification` for everything | The confidence thresholds above. |

## Tests

`tests/test_self_hosted_script.py` exercises the bootstrap script against fake
`docker`/`curl` (checksum handling, partial downloads, only the two services
started, timeout, `status`). `tests/live/test_live_self_hosted.py` is a smoke
test against the running stack (`SELF_HOSTED_ORS_URL`,
`SELF_HOSTED_GEOCODER_URL`); the heavy stack is **not** part of default CI.

## Attribution

Routing and geocoding data come from OpenStreetMap. Anything you publish from
this stack needs the credit "© OpenStreetMap contributors" and must respect the
[ODbL](https://www.openstreetmap.org/copyright) (see the
[attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)).
Geofabrik extracts carry the same licence.
