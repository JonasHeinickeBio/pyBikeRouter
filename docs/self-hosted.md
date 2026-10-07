# Self-hosted routing and geocoding

The planner talks to two public services by default: openrouteservice
(rate-limited, needs an API key) and Nominatim (1 request/second policy). The
`self-hosted` compose profile can replace either -- or both -- with local
services built from **one regional OSM extract**, so there is no public rate
limit and no key.

| Service | Image | Role |
| --- | --- | --- |
| `ors-self-hosted` | `openrouteservice/openrouteservice:v10.0.1` | routing, bicycle profiles only (`cycling-regular`, `-road`, `-mountain`, `-electric`) |
| `nominatim-self-hosted` | `mediagis/nominatim:5.3` | geocoding |

> **openrouteservice does not include a geocoder.** Its documentation says the
> geocoder endpoint "is not part of openrouteservice, but of our public API.
> It is not available when running an own instance of openrouteservice."
> That is why Nominatim runs next to it, and why `GEOCODER_PROVIDER=pelias`
> does *not* work against this stack ([geocoding.md](geocoding.md)).

## Pick the setup that fits your machine

You do not have to run everything locally. The two services are independent:
run the half you need and keep using the public service for the other.

| Setup | Routing | Geocoding | Start it with | Extra load on your machine |
| --- | --- | --- | --- | --- |
| **A. All public** (default) | public ORS (API key) -- or a local BRouter/Valhalla | public Nominatim | nothing | none |
| **B. Local routing only** | local ORS | public Nominatim | `scripts/self-hosted-bootstrap.sh --only routing` | ~1-2 GB RAM, ~1 GB disk (city extract) |
| **C. Local geocoding only** | public ORS | local Nominatim | `scripts/self-hosted-bootstrap.sh --only geocoding` | ~1-2 GB RAM, ~3 GB disk, a long first import |
| **D. Both local** | local ORS | local Nominatim | `scripts/self-hosted-bootstrap.sh` | B + C |

Which half helps most? Routing through the public ORS needs an API key and is
bound by its quota (check your provider's terms), whereas the public Nominatim
is only asked a few times per plan and the planner caches its answers
(`GEOCODER_CACHE_TTL_S`). So on a small machine **B** is usually the best first
step; **C** matters when you hit Nominatim's 1 request/second policy or want to
work offline. BRouter (below) is a lighter local router than ORS.

## Requirements

### Software

- Docker Engine with the Compose plugin (`docker compose`, v2).
- `bash` and `curl` (for the bootstrap script).
- Internet access on the first start only: the extract (Geofabrik), the images,
  and -- for ORS -- the SRTM elevation tiles. Afterwards both services run
  offline (Nominatim replication is not configured; the data does not update).
- Free host ports 8080 (ORS) and 8081 (Nominatim), or set `ORS_LOCAL_PORT` /
  `NOMINATIM_LOCAL_PORT`. Both bind loopback only.

### Hardware (measured)

All numbers below are from real runs on **one** machine (16 cores, SSD) with
the default **Bremen** extract (21 MB); memory is what `docker stats` reported
for the container. Your machine and region will differ -- treat them as a
floor and a data point, not a guarantee.

| Service | Configuration | First start until ready | Peak memory | Disk |
| --- | --- | --- | --- | --- |
| ORS | `ORS_XMX=512m ORS_MEM_LIMIT=1g` | ~2.3 min | 0.8 GiB | image 0.5 GB; graphs 0.12 GB; elevation cache 0.09 GB |
| ORS | `ORS_XMX=1g ORS_MEM_LIMIT=2g` | ~1.9 min | 1.2 GiB (1.07 GiB idle) | same |
| Nominatim | `NOMINATIM_THREADS=2 NOMINATIM_MEM_LIMIT=2g` | ~17 min | 862 MiB (857 MiB idle) | image 1.1 GB; database 1.6 GB |
| Nominatim | `NOMINATIM_THREADS=4` (default limit 4g) | ~11.5 min | not recorded | same |

Reading the table:

- **Disk** for both services on this extract is about **3.5 GB** (images +
  volumes + extract). Nominatim's database is large compared to the extract
  (1.6 GB for 21 MB), partly fixed overhead -- do not scale that ratio linearly
  to a bigger region; the
  [Nominatim import documentation](https://nominatim.org/release-docs/latest/admin/Import/)
  has sizes for larger imports.
- **RAM**: size the containers down with the knobs below, and leave room for
  Docker and the OS. For setup D with the small configurations the separately
  measured peaks add up to ~2 GiB; I would not run it on less than about 4 GB of
  total machine RAM (an estimate -- not tested on such a machine).
- **Time** is dominated by the one-time build/import; later starts take seconds
  because graphs and the database live in named volumes. Fewer Nominatim
  threads means a slower import, not a failed one.
- The compose *defaults* (`ORS_XMX=4g`, 6 GB / 4 GB container limits) are sized
  for a federal-state extract and were **not** measured; for a city-sized
  extract use the small configurations.
- **Bigger regions** (a federal state, a country) need proportionally more heap,
  disk and import time. That was not measured here: start with a small region
  and follow the upstream sizing guidance
  ([ORS](https://giscience.github.io/openrouteservice/run-instance/),
  [Nominatim](https://nominatim.org/release-docs/latest/admin/Import/)).

### Sizing knobs

| Variable | Default | Effect |
| --- | --- | --- |
| `ORS_XMX` / `ORS_XMS` | `4g` / `128m` | Java heap max / initial for ORS. A city-sized extract works with `512m`-`1g`. The initial heap must not exceed the maximum, or the JVM refuses to start. |
| `ORS_MEM_LIMIT` | `6g` | Docker memory limit for the ORS container; keep it above `ORS_XMX` (the JVM needs headroom). |
| `NOMINATIM_THREADS` | `4` | import/indexing threads; `2` halves the CPU/memory pressure and takes longer. |
| `NOMINATIM_MEM_LIMIT` | `4g` | Docker memory limit for the Nominatim container (tested down to `2g`). |

Set them in the shell (or an env file) when you run the bootstrap script, e.g.
`ORS_XMX=1g ORS_MEM_LIMIT=2g scripts/self-hosted-bootstrap.sh --only routing`.

## Quick start

```bash
scripts/self-hosted-bootstrap.sh                   # setup D: both services
scripts/self-hosted-bootstrap.sh --only routing    # setup B
scripts/self-hosted-bootstrap.sh --only geocoding  # setup C
```

It downloads the extract to `docker/self-hosted/data/region.osm.pbf`
(verifying the MD5 Geofabrik publishes next to it), starts the selected
service(s) and waits until they answer. It then prints the settings for the
half you started (the other half is left as you configured it):

```bash
ORS_BASE_URL=http://127.0.0.1:8080/ors     # routing
ORS_API_KEY=                               # may stay empty
GEOCODER_PROVIDER=nominatim                # geocoding
GEOCODER_BASE_URL=http://127.0.0.1:8081
GEOCODER_MIN_CONFIDENCE=0
GEOCODER_AMBIGUITY_MARGIN=0
```

Check on it any time with `scripts/self-hosted-bootstrap.sh status` (add the
same `--only` to look at one half), and with the API's
[`/readyz`](api.md#get-readyz) once it runs (ORS reports `ok` here, not
`unknown`: a self-hosted ORS has a health endpoint). Without the script, the
compose profiles are `self-hosted` (both), `self-hosted-routing` and
`self-hosted-geocoding`.

Both services bind **loopback only** (`127.0.0.1`); neither has authentication.
You can add the other half later with the same script and the same extract.

## Small-machine recipes

**B. Local routing, public geocoding** (the usual first step):

```bash
ORS_XMX=512m ORS_MEM_LIMIT=1g scripts/self-hosted-bootstrap.sh --only routing
```

```dotenv
ORS_BASE_URL=http://127.0.0.1:8080/ors
ROUTING_PROVIDER=ors
# geocoding stays public -- be a good citizen of the Nominatim usage policy:
GEOCODER_USER_AGENT=bike-routing-agent/0.1 (contact: you@example.com)
GEOCODER_CACHE_TTL_S=86400
```

**C. Local geocoding, public routing** (still needs your ORS API key):

```bash
NOMINATIM_THREADS=2 NOMINATIM_MEM_LIMIT=2g scripts/self-hosted-bootstrap.sh --only geocoding
```

```dotenv
GEOCODER_BASE_URL=http://127.0.0.1:8081
GEOCODER_MIN_CONFIDENCE=0
GEOCODER_AMBIGUITY_MARGIN=0
```

**Lighter local routing: BRouter.** BRouter has no graph-build or import step: it
reads downloaded segment tiles (about 125 MB per 5x5 degree tile) and its compose
service is capped at a 1.5 GB heap / 2 GB limit. See
[`docker/brouter/README.md`](../docker/brouter/README.md) and
[providers.md](providers.md). It pairs with either geocoder. (Not measured here
beyond those limits.)

**Stay public.** Setup A needs nothing from this document. The public services'
terms apply: Nominatim's usage policy (1 request/second, a real `User-Agent`),
and your ORS key's quota -- the planner's caches and the `/readyz` result caching
keep its own traffic low.

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
- **Other variables.** `NOMINATIM_DB_PASSWORD` (internal to the Nominatim
  container, the database port is not published); the sizing variables are in
  [Requirements](#sizing-knobs).

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
| ORS restarts in a loop with "Initial heap size set to a larger value than the maximum heap size" | `ORS_XMS` is larger than `ORS_XMX`; lower it (the default is `128m`) or raise the maximum. |
| ORS exits during graph build | Out of memory: raise `ORS_XMX` and `ORS_MEM_LIMIT` (keep the limit above the heap), or use a smaller extract. Check with `docker inspect <container> --format '{{.State.OOMKilled}}'`. |
| Nominatim exits or restarts during the import | Out of memory (or a full disk -- the database needs several GB): raise `NOMINATIM_MEM_LIMIT`, free disk space, or use a smaller extract. It was tested down to a 2 GB limit with 2 threads. |
| The import is very slow | Normal with few threads on a small CPU (17 min vs 11.5 min for Bremen with 2 vs 4 threads); wait it out -- it only happens once. |
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
