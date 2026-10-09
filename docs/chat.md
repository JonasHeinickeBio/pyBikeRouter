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
("Okay: at most 22.6 km (25 % shorter than the current route), avoid ferries."). "Shorter" without
any distance caps the distance at 75 % of the current route; "flatter" caps the climb at 70 % of the
current one; "longer" needs a distance to aim for and says so.

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
- **Sessions** are in memory (`MemorySaver`): a restart forgets them, at most `CHAT_MAX_SESSIONS`
  are kept (least recently used dropped first), a message is at most 500 characters, and a
  conversation keeps its last 40 messages. One turn at a time per session.

## Configuration

`CHAT_ENABLED` (default `true`; `false` = `POST /v1/chat` answers `503`, the panel is hidden),
`CHAT_MAX_SESSIONS` (default `200`). Free text additionally needs `LLM_PARSER_ENABLED`
([llm-parser.md](llm-parser.md)).

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
- Follow-ups ("shorter", "avoid ferries" ...) are understood by **rules**, not by a model: phrasing
  outside them gets "I did not catch what to change" plus examples. A model-based intent step could
  be added behind the same graph.
- Sessions are not persisted and not authenticated: anyone who can reach the API can chat; a session
  id (random, 128 bit) is the only handle.
- The web chat draws the route on the map but does not fill the form on the left.
- English only.
