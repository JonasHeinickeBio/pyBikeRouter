# Chat

A conversation that plans, changes and explains bike routes. It is a second LangGraph next to the
planning graph ([architecture.md](architecture.md)): the planning graph turns *one request* into a
route; the chat graph keeps a *conversation* and uses the planning graph whenever a route is
needed. Same code path as `POST /v1/route/plan` -- history, sight-stop fallback, errors.

Surfaces: `POST /v1/chat` ([api.md](api.md#post-v1chat)), the **Chat** panel of the web form, and
`bike-router chat` ([cli.md](cli.md#chat)).

## Ways to get a route

| Way | Example | Needs |
| --- | --- | --- |
| **One line** | `from Braunschweig to Goslar by gravel bike via Wolfenbüttel` · `Braunschweig -> Goslar` · `52.26,10.52 to 52.49,10.55` | nothing |
| **A loop** | `40 km loop from Goslar on a road bike` · `round trip around Braunschweig, 30 km` | nothing |
| **Past sights** | `Füssen to Oberammergau past 2 castles` · `... along the most famous sights` | the POI service ([pois.md](pois.md)) |
| **Step by step** | `plan a route` -> *Where do you want to start?* -> loop or destination -> distance or destination -> bike (quick answers for each) | nothing |
| **Your own words** | `a relaxed 50 km ride around Goslar tomorrow morning` | the LLM parser ([llm-parser.md](llm-parser.md)); without it, the step-by-step dialogue starts instead |
| **Change the last route** | `shorter`, `longer`, `flatter`, `avoid ferries`, `on a road bike`, `paved only`, `other direction`, `add a stop at Bicester`, `past 2 castles`, `use openrouteservice` | a previous route |
| **Pick an alternative** | `alternatives`, `the second one`, `the mtb version`, `the fastest`, `the flattest` | a previous route (no new routing) |

A one-line request can carry any of these, in any order -- the chat takes each clause out of the line
and what is left are the places:

| Clause | Examples | Becomes |
| --- | --- | --- |
| Bike | `by road bike`, `on my mtb`, `with a gravel bike`, `e-bike` | `bike_type` (only said as a bike: *Mountain View* stays a place) |
| Stops | `via Bad Harzburg and Ilsenburg`, `A to B to C`, `Munich über Rosenheim` | `via` |
| Out and back | `Goslar to Hannover and back` | round trip through the far place |
| Length | `40 km loop`, `80 km max`, `no more than 80 km`, `2 hour loop`, `90 minute loop` | loop length / `max_distance_km`; hours are turned into km at an assumed speed (road 25, gravel 20, mountain 15, touring 17, city 16, e-bike 22, else 18 km/h) **and the reply says so** |
| Climb | `max 400 m climb`, `at most 300 m of climbing` | `max_ascent_m` |
| When | `tomorrow at 8`, `on Saturday morning`, `next friday 7pm`, `2026-10-15 at 08:30`, `in 3 days`, `tonight` | `departure_time` in your time zone (a clock time that has passed today means tomorrow; a date without a time means 09:00) |
| Avoid / prefer | `no ferries`, `avoiding main roads`, `avoid unpaved and busy roads`, `paved only`, `quiet roads` | the avoid/prefer fields |
| Direction | `clockwise`, `counterclockwise`, `other direction` | `loop_direction` |
| Engine | `using openrouteservice` | `routing_engines` |
| Sights | `past 2 castles`, `2 sights`, `along the most famous sights` | `poi_stops` |
| German basics | `von Goslar nach Hannover`, `Rundtour`, `Schleife`, `3-4 Stunden`, `2 Stunden 30 Minuten`, `über` | the same |

The first answer starts with **how the line was read**, so a misreading is visible at once:
*"Planning Goslar to Hannover via Hildesheim (road bike, at most 80 km, no ferries, leaving Sun 11 Oct,
08:00)."* What was said but cannot be applied is listed after *Not applied:* and kept out of the places
("flat" without a climb limit, "along the Rhine", "with my kids", "for 3 days", a speed). A line the
rules cannot turn into places ("a nice ride", "40km", a question) starts the step-by-step dialogue, which
keeps what the line did say (a loop, 35 km, tomorrow, no ferries) and asks only for the rest.

An ambiguous place ("Neustadt") pauses the conversation: the chat lists the candidates and the next
message answers (`2`, `the first one`, or part of the name). A new request or `new route` instead
ends the question.

## Asking and getting things

`weather?` / `rain?` / `wind?` (the forecast of the plan), `how steep?` / `how far?` / `what surface?`
(from the plan's numbers), `why this route?` / `pros and cons` (the scoring's own facts),
`what sights are along the way?` (best-known places near the route, by fame), `send me the GPX`
(the files of the selected route), `help`, `new route`. Answers are built from the plan's data, not
generated: no safety claims, and "no forecast" is said when there is none.

What a change does: it edits the **previous request** and plans again, and says what it did
("Okay: at most 22.6 km (25 % shorter than the current route), avoid ferries."). The start can be
changed too (`leave tomorrow at 8`, `on Saturday morning`). "Shorter" without
any distance caps the distance at 75 % of the current route; "flatter" caps the climb at 70 % of the
current one; "longer" needs a distance to aim for and says so.

Every route reply reports **surface** and **traffic**, from the same numbers the web form and the CLI
show: *"Surface: 71 % paved, 24 % unpaved, 5 % unknown. Traffic (estimate): 5 % busy roads, ..."*
Traffic is an **estimate** from the road tags, in four bands -- busy roads, some traffic, quiet
streets, paths and tracks (class of road, cycle track or lane beside it, speed limit, access; see
[api.md](api.md)) -- not a count of cars: *"Traffic (estimate): 27 % busy roads, 54 % some traffic,
12 % quiet streets, 7 % paths and tracks."* Asked, the chat also gives the older figure (the share on
main roads without a bike lane). Surface is the OSM
`surface` tags the engine routed over, and "unknown" is never counted as paved. When the engine gave
no tags the reply says so ("the engine gave no road data") instead of staying silent. Alternatives
list their main-road and paved shares side by side, and `how much traffic is there?`, `is it busy?`,
`what is the surface like?` answer with the same numbers (`is it safe?` gets them plus "I cannot
judge safety"). `avoid busy roads` / `paved only` still change the route.

Limits are goals the engines are steered towards, not guarantees. When the route misses one, the reply
says so ("Note: it still climbs 335 m, more than the 234 m asked for -- the engines found nothing closer"; a
loop more than 20 % off its length likewise). When a service did not answer in time, the reply offers
**Try again**, which re-sends exactly the request that failed.

Candidates of an ambiguous place that are the same town (within 5 km, or a county named after it) are
not asked about; "Springfield" in two states still is.

## The form and the chat

The web page sends the chat what the form holds, so "make it shorter" changes *that* trip:

- a route you planned **in the form** (it is on screen, the chat did not draw it): the form's request
  and the route are sent with your next message, once. "Make it shorter", "alternatives", "the mtb
  version", "send me the GPX" and "what sights are along the way?" then work on it, in a new
  conversation as well;
- the form **edited** after a route (a new start, another bike type): the form's request is sent
  and replaces the one the conversation remembers (a request that had failed no longer applies),
  while the route on screen stays the one the chat drew until the new one is planned;
- nothing otherwise: when the chat drew the route and the form was not touched, the chat already
  knows it.

The server holds the form's request to the plan request's rules and its route to the plan response's,
and drops what does not pass (a half-filled form, a route that is not `ready`): a message is never
refused because of its context. The form is read at the moment of sending; a place picked on the map
without typing is only noticed once a plan is made. See `context` in [api.md](api.md#post-v1chat).

## What it could not read, and how well it reads

The chat reads messages with rules. Before anyone puts a language model behind them, there should be
numbers for how well the rules do and a record of what people actually say.

**A benchmark** -- `src/bike_routing_agent/chat/data/chat-v1.json` (shipped with the package, so `chat eval` runs from an install and from the image too): 140 messages, each with the situation it arrives in (nothing
planned, a route on screen, a question waiting, a failed request) and the intent a sensible person would
call it (`plan`, `plan_text`, `guided`, `refine`, `alternatives`, `sights`, `explain`, `export`,
`optimize`, `reset`, `help`). The expectations say what the chat *should* do, not what it happens to do.

```bash
bike-router chat eval                    # with a free-text parser configured: 125 of 140 (89.3 %)
bike-router chat eval --rules-only       # without one: 122 of 140 (87.1 %)
bike-router chat eval --format json --fail-under 0.85
```

It runs the rules only -- nothing is planned and no model is called, so it is free and offline (unless
you add `--intent-model`, below), and a test keeps a floor under the score. The table gives recall and precision per intent. What the rules
read wrongly today is the useful part: things people say about sights (*points of interest*, *what is
there to see*), changes phrased as a request (*no ferries please*, *with a stop in Celle*, *drop the
stop*), and the plain starters (*plan a route*, *let's go*), which go to the free-text parser instead of
the dialogue when one is configured.

**A model behind the rules** -- off by default (`CHAT_INTENT_MODEL=true`, with a language model configured
as for [free text](llm-parser.md)). The rules still go first. Only a message they give up on -- the
ones that would otherwise be planned as a new trip (`plan_text`) or sent to the dialogue -- is put to
the model, which answers with **one word** from a fixed list (`plan_text`, `guided`, `refine`,
`alternatives`, `sights`, `explain`, `export`, `optimize`, `help`). It is deliberately small:

- It only *routes*. The handler it picks reads the message with the same rules as always, so the
  model can send *what is there to see* to the sights lookup, but it cannot put a place, a distance or
  a bike type into a request. Nothing the model writes is used except that word.
- The word must make sense where the person is: with no route on screen, `alternatives`, `sights`,
  `explain`, `export` and `optimize` are not offered (and a reply naming one is refused); with a failed
  request `refine` is (*try another engine*).
- An error, a timeout, a reply that is not one of the allowed words: no answer, and the chat does what
  it does without a model. The answer to an open question of the dialogue never goes to the model.
- The message goes in as data between `<message>` tags (at most 500 characters), and **to the model
  you configured**, exactly as free text does; with a hosted model that means it leaves this machine.

```bash
LLM_PARSER_ENABLED=true LLM_PROVIDER=openai LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  LLM_MODEL=llama3.2:3b bike-router chat eval --intent-model
```

scores the benchmark again with the model behind the rules and prints the rules' own score next to it,
how many messages the model placed and how many of those it got as expected, and marks its
placements `(model)` in the list of differences. It makes one model call per message the rules give up
on (about 23 of the 140, all made-up examples). Use it before switching `CHAT_INTENT_MODEL` on, and
again when you change the model: a small model can place a message worse than the plain fallback.

**A miss log** -- off by default. Set `CHAT_MISS_LOG_PATH=./data/chat/misses.jsonl` and every message
the rules could not read is appended there, one line of JSON each: the message (cut at 500 characters),
the kind -- `unread` (free text that told the dialogue nothing), `refine_unknown` (a change it could not
make out) or `text_failed` (the language model could not read it) --, the intent it fell to, whether a
route was on screen, and the time. It stays in that file on this machine: nothing is sent anywhere, no
session id is stored, and the file is capped at about 2 MB (the old part is kept once as `.1`).
Treat it like the conversation store: it holds what people typed.

```bash
bike-router chat misses                  # how many, of which kind, and the phrases that come back
bike-router chat misses --export-cases new-cases.json    # the distinct ones, unlabelled
bike-router chat misses --clear          # delete the log
```

An exported case has `"expected": null`: a person decides what the chat *should* have done and fills it
in, and only then does it belong in the benchmark (the loader refuses an unlabelled one). That loop --
log, judge, add, measure -- comes before any model-based reading of messages, so a model has something to
beat and is only ever asked about messages the rules gave up on.

## Optimising

`Braunschweig to Wernigerode with less traffic but as much asphalt as possible` -- or, on the route on
screen, `optimise for the quietest and smoothest` -- fetches several routes and picks the best fit for
the goals ([optimizer.md](optimizer.md)). The reply says how it was read ("optimised for less traffic
and more asphalt"), what was compared, why this route won (with the numbers), what the best of each
goal had instead and what it cost, and the next best routes; `alternatives` lists all of them. Goals
come in the order said, with weights 1.0, 0.7, 0.5, 0.4. One mild wish ("quieter", "paved only") stays
an ordinary change; two goals, "optimise", or a superlative ("the quietest, smoothest route") make it
an optimisation (sights: "as many sights as possible", "with the most sights along the way";
"past the most famous sights" stays a request for stops, [pois.md](pois.md)). Quiet and flat have many
wordings: *less / little / no traffic*, *no busy roads*, *away from busy roads*, *quiet*, *calm*; *flat*,
*little / less / fewer climbing / hills*, *not too hilly / steep*, *avoid hills*; and in German *wenig /
kein Verkehr*, *ruhig*, *kaum / wenig Steigung*, *flach*. A lone "flat" or "a bit flatter" is still an
ordinary change; "quiet and flat" (two goals) is an optimisation -- except a single superlative that names one of the routes already found ("the
fastest", "the quietest"), which just picks it. It takes 30-60 s, because many routes are computed.

## How it works

```
START -> ingest -> router --+-> plan_one_line --+
                            +-> plan_text ------+
                            +-> guided ---------+-> plan_route -> clarify <-> plan_route -> finish
                            +-> refine ---------+
                            +-> alternatives / sights / explain / export / reset / help -> finish
```

- **State** (`chat/models.py::ChatState`): the messages, the open question of the dialogue, the last
  request and plan, a request that failed (so *use openrouteservice* retries exactly it), pending
  ambiguity. LangGraph's checkpointer keeps it per `thread_id` = the chat session.
- **Intent** (`chat/graph.py::decide_intent`, `chat/rules.py`): rules, in this order -- `reset`/`help`,
  a complete one-line request, an open question, a follow-up (change/alternatives/explain/...), free
  text for the LLM parser, otherwise the dialogue. Rules never invent anything: places stay the
  words typed (the geocoder looks them up), a coordinate typed as `lat, lon` becomes one.
- **Ambiguous places** use LangGraph's `interrupt`: the graph pauses in `clarify`, the service sees
  the open question and resumes it with the next message (`Command(resume=...)`).
- **Planning** goes through an injected `Planner` (`api._ApiPlanner`: `plan_route` /
  `plan_route_from_text`), sights through `Sights` (`api._ApiSights`: the POI service) -- the graph
  has no idea about HTTP, so tests drive it with scripted fakes.
- **Sessions** are kept in memory by default (a restart forgets them). With `CHAT_STORE_PATH` set they
  are written to a SQLite file after every turn and found again after a restart or a redeploy (see
  below). Either way at most `CHAT_MAX_SESSIONS` are kept (least recently used dropped first) and only
  the latest checkpoint of each conversation (not its step-by-step history), a message is at most 500
  characters, and a conversation keeps its last 40 messages. One turn at a time per session.

## Configuration

`CHAT_ENABLED` (default `true`; `false` = `POST /v1/chat` answers `503`, the panel is hidden),
`CHAT_MAX_SESSIONS` (default `200`). Free text additionally needs `LLM_PARSER_ENABLED`
([llm-parser.md](llm-parser.md)).

### Conversations that survive a restart

`CHAT_STORE_PATH=/path/to/sessions.sqlite` keeps the conversations in that file (the folder is
created); `CHAT_SESSION_DAYS` (default `30`) drops the ones not used for that long when the service
starts. A paused question (an ambiguous place, the next question of the step-by-step dialogue) is still
open afterwards, and `GET /v1/chat/{session_id}` returns what was said, the quick answers and the last
route. The web panel remembers its session id in the browser and restores the conversation, the quick
answers and the route when you open it again.

- `bike-router deploy up` mounts `~/.local/share/pybikerouter/chat` (`--chat-dir`; `--no-chat-store`
  for memory only), and the easy-install kit keeps it in `data/chat`.
- The file holds JSON, never pickled objects, and only the latest checkpoint of each conversation. A
  conversation that cannot be read back (a damaged row, or one written by another LangGraph checkpoint
  layout after an upgrade) is dropped and starts over; a file that cannot be opened at all leaves the
  chat in memory with a log message instead of stopping the service.
- It stores what people typed and the routes the chat drew, in plain text on disk: treat the file like
  the exported routes. Deleting it forgets every conversation -- or one at a time, from the web panel
  (below).

### New chat, earlier conversations, delete

The web panel has a **New chat** button (the old conversation stays stored, the map keeps its route) and
an **Earlier conversations** list: title (the first thing asked), age, number of messages; click one to
open it again -- its messages, quick answers and last route replace what is shown -- and **×** deletes it
after asking (its messages and its file entry are removed from the server).

The list is **this browser's**: the browser keeps only the ids of the conversations it has had
(`localStorage`, at most 30) and asks the server about those ids (`POST /v1/chat/summaries`); ids the
server no longer knows (expired, dropped for newer ones) disappear from the list. There is deliberately
no endpoint that lists conversations nobody named, so a session id remains the one handle to a
conversation (it is not discoverable by someone else who can reach the API) and a conversation started
on the phone does not show up on the laptop.

## Evidence and limits

Verified (2026-10-09): 100+ tests with a scripted planner (every way above, changes, alternatives,
questions, clarification with pause/resume, failures, session limits); the same conversation
**for real** through the CLI and in the browser against BRouter, Nominatim and Overpass: a one-line
request, alternatives and picking the mountain-bike one (drawn on the map), "how steep",
"make it shorter" / "avoid ferries and use a road bike" (re-planned), the files, the step-by-step
dialogue, sights along the route (real fame ranking), "other direction", and a real ambiguous
place ("Neustadt": five candidates, choice by number, resumed).

Not verified / not covered:

- **Free text with a language model** was only tested with a stub: no model credentials were used.
- Everything above is read by **rules**: a phrasing outside them falls back to the step-by-step dialogue
  (or the language model, when enabled). The rules were checked with more than 100 phrasings (every case in
  `tests/chat/test_oneline.py` is validated with the API's own request model) and with live
  conversations; they will still meet phrasings nobody tried. Places are looked up as typed, so a
  misspelt place is the geocoder's to find.
- Follow-ups ("shorter", "avoid ferries" ...) are understood by **rules**, not by a model: phrasing
  outside them gets "I did not catch what to change" plus examples. A model-based intent step is available behind the same graph (`CHAT_INTENT_MODEL`, above).
- Sessions are persisted only when `CHAT_STORE_PATH` is set, and never authenticated: anyone who can
  reach the API can chat and read a session's history; a session id (random, 128 bit) is the only
  handle.
- The web chat draws the route on the map and fills the Plan form on the left with the request it
  was planned from (places, bike type, distance, preferences, loop), so you can adjust it there and
  plan again. It also tells the chat what the form holds (see below), so the two stay one workspace.
- English only.
