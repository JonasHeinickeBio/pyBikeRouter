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
| **[DWD via Bright Sky](https://brightsky.dev/docs/)** (Germany) | free, **no key** | stations in Germany (a few km beyond the border too) | yes: wind and direction, **gusts, precipitation probability**; no UV, no feels-like | DWD data is open for **any use incl. commercial** (credit "Deutscher Wetterdienst"); Bright Sky itself is a volunteer-run service with no SLA; per-station, one request per point |
| **[MET Norway Locationforecast](https://api.met.no/weatherapi/locationforecast/2.0/documentation)** (fallback) | free, **no key**, needs an identifying `User-Agent` | global, ~9 days | wind and direction yes; no gusts, probability or UV in the compact format | one request per location; CC BY 4.0 / NLOD; generic client names get 403 |
| NOAA / NWS | free, no key | US only | yes | US only |
| OpenWeatherMap, WeatherAPI.com | free tier but **API key** | global | partly | key and account, small free quotas |

So the default (`WEATHER_PROVIDER=auto`) tries, in order: **DWD for routes
entirely inside Germany** (the authoritative source there, and the one whose
data licence allows commercial use), then **Open-Meteo**, then **MET Norway**.
All are keyless; a plan costs one Open-Meteo request (several locations per
call) or one Bright Sky request per forecast point (at most five), and if a
provider is down or rate-limiting us the next one answers instead of the
feature disappearing. DWD declines a route with any point outside Germany, so
those go straight to Open-Meteo.

DWD publishes **no UV index and no feels-like temperature** and it is a
per-station forecast, so by default the gaps are filled from a second source
(next section). To use Open-Meteo's grid forecast alone set
`WEATHER_PROVIDER=open-meteo` (keeping its non-commercial limitation in mind),
or `dwd` to use DWD alone.

### Merging sources

With `WEATHER_MERGE=true` (the default) the first provider that answers is the
**primary** and its values are never replaced. Only the *optional* fields it
leaves missing (feels-like, UV index, gusts, precipitation probability) are
taken from a later provider **that can supply them**, matched by point and hour.
Nothing is averaged: two forecast models disagreeing is not settled by splitting
the difference, and every value has one origin.

- In Germany that means DWD for wind, rain and temperature, plus Open-Meteo's UV
  and feels-like (and any hour where DWD has no gust or rain probability).
- Outside Germany Open-Meteo has all the optional fields, so nothing is merged
  and MET Norway (which supplies none of them) is only the fallback.
- A donor is asked only when something is actually missing, concurrently with
  the others, and its failure is ignored -- the primary forecast is still
  returned. Each provider's result is cached on its own.
- `route.weather.sources` lists every provider whose data is in the forecast
  (primary first), the credits are combined, and the card links all of them.

Two consequences to know: the route's coordinates go to **every** provider that
is asked (set `WEATHER_MERGE=false` to send them to the primary only), and if you
chose DWD for its commercial-use licence, a merged forecast also contains
Open-Meteo data, whose free tier is non-commercial -- turn merging off (or use
`WEATHER_PROVIDER=dwd`) in that case. The free Open-Meteo tier
is for non-commercial use; a commercial deployment needs their subscription (or
`WEATHER_PROVIDER=met-no` and its terms).

**Privacy.** The coordinates of the route (a handful of points, rounded to
~5 km grid cells) are sent to the chosen provider (and to a second one when
[merging](#merging-sources) fills gaps). Set `WEATHER_PROVIDER=none`
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
5. **Summarise**: temperature range, "feels like" range (Open-Meteo only; MET
   Norway has none, so it stays missing), rain probability and rate, wind mean/
   max/gusts, mean headwind and the share of the route with at least 10 km/h of
   head or tailwind, UV, the worst condition and whether part of the ride is
   after dark.
6. **Where the rain is** (`summary.wet_stretch`): the first to the last sample
   that is wet (precipitation probability >= 60 %, >= 0.3 mm/h, or a rain/snow/
   storm condition), as kilometres along the route and the clock times there.
   It is bounded by forecast *samples*, so it is approximate.
7. **Daylight** (`daylight`): sunrise and sunset at the start of the route on the
   day of the ride, **computed** from date and place (NOAA solar formulas, within
   a couple of minutes) rather than fetched, so it works with every provider and
   offline. `minutes_of_light_left_at_arrival` is negative when you arrive after
   sunset. In polar day/night there is no sunrise or sunset and the field is
   empty.

## The wind's effect on the travel time

The engine's duration assumes still air. `route.weather.wind_effect` estimates what the forecast wind does
to it -- `{still_air_s, adjusted_s, effect_s, range_s: [low, high]}`, `effect_s` positive = slower. The weather
card shows it as **Wind on your time** ("+12 min (9-15)") and an advisory says it in words; each
departure option carries `wind_effect_s` for its own hour (the comparison of leaving earlier or later is
partly a comparison of the wind).

**Method** (`weather/wind_time.py`): the standard cycling power equation,
`P = (Crr*m*g + m*g*grade + 1/2*rho*CdA*(v + w)*|v + w|) * v`, with `v` the ground speed and `w` the
headwind. The power is the one the engine's still-air pace needs; the speed in the wind is the one that needs
the *same power*, solved for each stretch between forecast points from the mean of the headwinds at its two
ends (the ends take the nearest point). That gives a **factor on the engine's duration**, so what the
engine knows about surfaces and climbs stays in the number; the route's mean climb (`ascent / distance`)
takes its share of the power, so the wind matters less on a hilly ride. The rider is a typical one per
bike type (mass with bike and load, rolling resistance, frontal area: road 85 kg / 0.0045 / 0.36 m^2,
gravel 88 / 0.007 / 0.42, touring 100 / 0.006 / 0.55, mountain 88 / 0.010 / 0.50, city 95 / 0.006 / 0.58,
commuter 88 / 0.005 / 0.45, recumbent 95 / 0.005 / 0.30). The model reproduces the textbook figure of
roughly 300 W for a road bike at 40 km/h on the flat (338 W here). The range varies the frontal area, the
least certain input, by +-25 %.

**What it is not** -- read these before relying on a number:

- **Same effort as in still air.** Real riders push harder into a headwind and ease off with a tailwind, so
  the estimate is on the pessimistic side for headwinds and on the optimistic side for tailwinds (it gives
  more than people typically gain). A 15 km/h headwind at a 20 km/h pace comes out at about 58 % more time.
- **Not validated** against measured rides; the range is the model's parameter uncertainty only.
- **Along-track wind only**: crosswinds, gusts and shelter (forest, buildings) are ignored, and the forecast
  points are kilometres apart (at most five).
- **Not fed back**: the forecast hours are still picked with the engine's still-air duration, so a long
  ride with a strong wind has slightly shifted hours.
- **Only with the engine's own duration** (not the 15 km/h fallback) and **not for e-bikes** (the motor's
  assistance changes with speed, so "the same power" means nothing): `wind_effect` is `null` there.
- Stated (advisory, card, option) only from 3 minutes or 3 % of the ride, whichever is larger.
- Information only: it does not change the plan, the ranking or the score.

On the live forecast for Braunschweig -> Hannover (2026-10-20, a 22 km/h east wind, riding west, mean tailwind
13 km/h) it comes out at about -49 min on the engine's 3 h 05 min for a road bike (range -50 to -47).

## In the exported files

The GPX and GeoJSON of a route with a forecast carry it: every forecast point is a GPX waypoint
(`Forecast 14:01 UTC`, the time the rider is expected there and a line such as `8 °C, rain 2 %, wind
19 km/h from E, tailwind 12 km/h`), and the GPX header and the GeoJSON `forecast` block say when the
forecast is for, the estimated effect of the wind on the time and **the data credit** (the data is
CC BY: the credit travels with the file). Details: [scoring-and-exports.md](scoring-and-exports.md).
The forecast is a snapshot taken when the plan was made; a file does not update itself.

## Other departure times

"Should I leave now or later?" The same route is also evaluated for departures
from `WEATHER_OPTION_HOURS_BEFORE` hours before to `WEATHER_OPTION_HOURS_AFTER`
hours after the one you asked for (defaults 3 and 6, hourly; set both to `0` to
switch it off). The forecast is fetched **once** for the widened window, so it
costs no extra request.

`route.weather.departure_options` lists them in time order, each with how many of
its forecast points are wet, the highest rain probability, wind and headwind,
temperature, and whether the ride starts before sunrise or ends after sunset.
Rules, so the list can be trusted:

- Options the forecast does not cover **completely** are left out, never compared
  partially; options in the past are not offered; your own time is always included.
- There is **no score**. `suggested_departure` is set only when another start is
  strictly better on, in this order, (1) fewer wet points and (2) daylight, ties
  going to the start closest to yours. Wind and temperature are shown but not
  weighed: that would need calibration evidence, like every other weather weight.
- Less rain is **never bought with darkness**: an option before sunrise or after
  sunset is not suggested in place of a daylight one.
- A suggestion also becomes an advisory, worded without clock times:
  "Leaving 2 hours earlier would avoid the forecast precipitation on this route."

The web card has an *Other departure times* list (open when there is a
suggestion); choosing a row sets the departure picker and plans again for it.

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
| Where the rain is | any wet sample: "between about km 12 and km 31", "around km 20" or "along the whole route" |
| Feels like | at least 3 degrees below the air temperature and at or below 6 C |
| UV | index >= 6 |
| Sunrise / sunset | the ride starts before sunrise, ends after sunset, or sunset follows arrival within 45 minutes |
| After dark | only when sunrise/sunset cannot be computed (polar day/night): the forecast's daylight flag is off for some sample |

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
  temperature range, "feels like" (when it differs by 2 degrees or more), rain
  chance, **where and when the rain is** (kilometres and local clock times),
  sunrise / sunset in your local time, wind with direction, a headwind/tailwind bar,
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

See [configuration.md](configuration.md): `WEATHER_PROVIDER` (`auto`, `dwd`, `open-meteo`,
`met-no`, `none`), `WEATHER_USER_AGENT` (MET Norway requires you to identify
your app; also sent to Bright Sky), `WEATHER_TIMEOUT_S`, `WEATHER_CACHE_TTL_S`, `WEATHER_MERGE`, `WEATHER_OPTION_HOURS_BEFORE`/`WEATHER_OPTION_HOURS_AFTER`, `WEATHER_MAX_SAMPLES`,
`WEATHER_SAMPLE_SPACING_KM`, and the two base URLs.

## Not included

- **No effect on the score or ranking**: weights for weather would need the same
  calibration evidence as every other term ([scoring-and-exports.md](scoring-and-exports.md)).
- The wind's effect on the travel time is an estimate and shown beside the engine's duration, which is
  never replaced (see above).
- No radar nowcast and no historical weather.
- The departure comparison is for the route you got: it does not re-route (a
  different time never changes the path) and compares hourly steps only.
- MET Norway's compact format has no gusts, probability or UV, and DWD has no UV
  or feels-like, so those stay missing (reported as missing, never as zero) when
  they answer.

## Tests

`tests/weather/` (offline): condition mappings, the sampling and wind maths with
known values, sunrise/sunset against published London solstice times, the rain
stretch and feels-like rules, the departure comparison and its suggestion rule, the providers against responses captured from the real APIs
(`tests/fixtures/open_meteo_*.json`, `met_no_compact_response.json`,
`dwd_brightsky_response.json`), including DWD's Germany-only coverage rule, the
service's fallback, caching and gap-filling merge, and the node. `tests/test_frontend_weather.py`
runs the UI helpers under node. `tests/live/test_live_weather.py` calls the real
services (`pytest -m live`). The suite sets `WEATHER_PROVIDER=none` so no test
can reach a real weather API by accident.
