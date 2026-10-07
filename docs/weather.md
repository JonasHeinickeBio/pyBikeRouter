# Weather along the route

Every ready plan can carry the **forecast for the ride**: temperature, rain,
wind (resolved into head/tail/cross wind against the direction you are actually
riding), gusts, UV and daylight, sampled where and *when* you will be on the
route. It is informational -- it never changes the plan's outcome and it is not
part of the score.

```
POST /v1/route/plan  { ..., "departure_time": "2026-10-08T07:30:00Z" }   # optional, default: now
-> route.weather = { summary, samples[], advisories[], attribution, ... }
-> weather_status = "ok" | "unavailable" | "not_covered" | "skipped" | null
```

## Where the data comes from

Requirements: free, keyless (no account to run), reliable, global, hourly, with
wind direction. I compared the options that fit; the numbers are from each
provider's own terms (checked 2026-10).

| Provider | Cost / key | Coverage | Hourly wind dir. + gusts | Catch |
| --- | --- | --- | --- | --- |
| **[Open-Meteo](https://open-meteo.com/en/terms)** (primary) | free, **no key**; 600 calls/min, 5,000/h, 10,000/day | global, up to 16 days | yes, plus precipitation probability, UV, daylight flag, WMO weather code | **non-commercial use only**, data CC BY 4.0 (credit required); a subscription removes both limits |
| **[MET Norway Locationforecast](https://api.met.no/weatherapi/locationforecast/2.0/documentation)** (fallback) | free, **no key**, needs an identifying `User-Agent` | global, ~9 days | wind and direction yes; no gusts, probability or UV in the compact format | one request per location; CC BY 4.0 / NLOD; generic client names get 403 |
| Bright Sky (DWD) | free, no key | Germany-focused | yes | poor outside Germany |
| NOAA / NWS | free, no key | US only | yes | US only |
| OpenWeatherMap, WeatherAPI.com | free tier but **API key** | global | partly | key and account, small free quotas |

So the default is **Open-Meteo, with MET Norway as an automatic fallback**
(`WEATHER_PROVIDER=auto`): both are keyless, a plan costs **one** Open-Meteo
request (several locations per call), and if it is down or rate-limiting us the
fallback answers instead of the feature disappearing. The free Open-Meteo tier
is for non-commercial use; a commercial deployment needs their subscription (or
`WEATHER_PROVIDER=met-no` and its terms).

**Privacy.** The coordinates of the route (a handful of points, rounded to
~5 km grid cells) are sent to the chosen provider. Set `WEATHER_PROVIDER=none`
to switch the feature off completely -- then no weather request ever leaves the
service.

## How the forecast is applied to a route

1. **Sample** the geometry about every `WEATHER_SAMPLE_SPACING_KM` (10 km), at
   least the start and end, at most `WEATHER_MAX_SAMPLES` (5). Forecast grids
   are kilometres wide, so denser sampling would only duplicate answers.
2. **Time** each sample: `departure + duration x fraction along the route`. The
   engine's duration is used; if it reports none, it is estimated at 15 km/h and
   the result says so (`duration_source: "estimated"`).
3. **Look up** the unique grid cells of all returned candidates in one call, for
   the hours covering the ride, and pick the forecast hour nearest to each
   sample (a sample more than 90 minutes from any forecast hour is "not covered"
   and dropped, never guessed).
4. **Wind vs. travel.** `wind_from_deg` is where the wind blows *from*. With the
   local direction of travel `b`, headwind = `speed x cos(from - b)` and
   crosswind = `speed x sin(from - b)` (positive crosswind = from the right).
   Because each candidate has its own geometry, candidates differ in how much
   headwind they have, which is what the table's *Wind* column compares.
5. **Summarise**: temperature range, rain probability and rate, wind mean/max/
   gusts, mean headwind and the share of the route with at least 10 km/h of head
   or tailwind, UV, the worst condition and whether part of the ride is after
   dark.

## Advisories

Short facts worth stating, derived from the summary -- never a statement about
safety (the explanation still ends "not a guarantee of safety"):

| Fact is mentioned when | Threshold |
| --- | --- |
| Thunderstorms / snow or freezing rain / fog | in the forecast along the route |
| Rain | precipitation probability >= 60 % or >= 1 mm/h (not repeated when storms/snow already stated) |
| Wind gusts / sustained wind | >= 50 km/h / >= 30 km/h |
| Headwind / tailwind | mean >= 15 km/h |
| Cold / freezing / heat | <= 3 C / <= 0 C / >= 32 C |
| UV | index >= 6 |
| After dark | the forecast's daylight flag is off for some sample |

These thresholds decide what is *mentioned* (`weather/analysis.py`); they are
not calibrated against anything and not used for ranking.

## Failure behaviour

Weather is best effort. A provider outage, rate limit, a departure beyond the
forecast range (`departure_time` more than 14 days ahead is rejected with 422;
a time more than an hour in the past is read as "now") or an unusable geometry
leaves `weather: null` and sets `weather_status`; the plan is unaffected.
Providers are tried in order, results are cached per provider (30 minutes by
default; shared across instances with the [Redis cache](providers.md#shared-cache-redis)),
and failures are never cached. `/readyz` lists a `weather` component that can
only degrade the instance, never make it unready.

## In the web UI

- **When**: depart now, in an hour, this evening, tomorrow morning, or any date
  and time (your local time, up to 14 days ahead).
- **Weather card** for the candidate you are inspecting: conditions and
  temperature range, rain chance, wind with direction, a headwind/tailwind bar,
  the forecast hour by hour along the ride, advisory notes, and the provider's
  required attribution.
- **Map**: a marker per forecast point on the route (hover for the details).
- **Candidates table**: a *Wind* column (`▲` average headwind, `▼` tailwind) to
  compare routes.
- The page also follows your system's dark mode.

## From the CLI

`bike-router route plan --origin ... --destination ... --departure-time 2026-10-08T07:30:00Z`
prints the same `route.weather` object (default: now; `WEATHER_PROVIDER=none`
switches it off).

## Configuration

See [configuration.md](configuration.md): `WEATHER_PROVIDER` (`auto`, `open-meteo`,
`met-no`, `none`), `WEATHER_USER_AGENT` (MET Norway requires you to identify
your app), `WEATHER_TIMEOUT_S`, `WEATHER_CACHE_TTL_S`, `WEATHER_MAX_SAMPLES`,
`WEATHER_SAMPLE_SPACING_KM`, and the two base URLs.

## Not included

- **No effect on the score or ranking**: weights for weather would need the same
  calibration evidence as every other term ([scoring-and-exports.md](scoring-and-exports.md)).
- **No wind-adjusted travel time**: durations are the engine's.
- No radar nowcast, historical weather, or sunrise/sunset beyond the daylight flag.
- MET Norway's compact format has no gusts, probability or UV, so those stay
  missing (reported as missing, never as zero) when it answers.

## Tests

`tests/weather/` (offline): condition mappings, the sampling and wind maths with
known values, both providers against responses captured from the real APIs
(`tests/fixtures/open_meteo_*.json`, `met_no_compact_response.json`), the
service's fallback and caching, and the node. `tests/test_frontend_weather.py`
runs the UI helpers under node. `tests/live/test_live_weather.py` calls the real
services (`pytest -m live`). The suite sets `WEATHER_PROVIDER=none` so no test
can reach a real weather API by accident.
