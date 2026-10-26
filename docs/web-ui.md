# The web planner: places and the map

The page that opens at `http://localhost:8000` (or your port) plans on a form at the left and
draws on the map. This page covers how places get into the trip; the rest is in the pages for
[weather](weather.md), the [optimiser](optimizer.md), the [chat](chat.md) and
[phones](mobile.md).

## Putting places into the trip

Four ways, all ending up in the same fields (Origin, Destination, the Via stops):

- **Type** a place name or `lat, lon` (for example `52.27, 10.53` to skip geocoding).
- **Pick on map** (the `◉` button next to a field), then click the map: that field gets the point.
- **Click anywhere on the map** -- no button first -- to *select a point* and choose where it goes
  (below).
- **Chat** -- a route the chat plans fills the form with its request, so you can adjust it there.

## Elevation profile

Below the numbers of a route the page draws its **elevation profile**: elevation over distance, coloured
by the grade of each stretch (steep descent, descent, flat, climb, steep climb, very steep climb; the
legend is under the chart), with the highest and lowest point, the steepest 100 m and the biggest
climbs. Move the pointer (or a finger) over the chart to read the distance, elevation and grade at that
point; the same spot is marked on the map. With the keyboard, focus the chart and use the arrow keys
(Home and End jump to the ends). It follows the route you are looking at: click another alternative and
the chart changes.

How it is made (`frontend/profile.js`):

- from the elevation each route vertex carries (BRouter returns it for every vertex; a route whose
  geometry has no elevation on every vertex, or that is shorter than 200 m, gets no chart rather than
  a guessed one);
- the raw elevations are noisy -- a vertex every ~20 m with a metre or so of jitter, which summed up
  overstates the climbing by 10-15 % against the engine's own figure -- so the chart works on elevations
  averaged over 100 m of route. The totals shown elsewhere (ascent, descent) stay the engine's: on the
  Goslar - Brocken test routes the smoothed climbing came out within 7 % of them;
- *steepest 100 m* is the biggest rise or fall over 100 m of that smoothed profile (shown from 6 %);
- a *climb* is a stretch that gains at least 25 m over at least 400 m at an average of at least 3 %; a dip
  of up to 10 m does not end it. The three with the biggest gain are listed. They are the profile's own
  reading, not the engine's;
- the vertical axis spans at least 40 m, so a flat ride looks flat instead of like a mountain range.

## Colour the route

Under the metrics, *Colour the route* switches the active route on the map between **Plain**,
**Surface** (paved / unpaved / cobbles / not mapped) and **Traffic** (busy / some traffic / quiet /
off-road and bike paths / not mapped) or **Gradient** (steep descent ... very steep climb). The legend gives what each colour covers in km and percent, and
one line names the longest unpaved (or busy) stretch with where it starts. The choice is remembered in
this browser.

- The colours come from the OSM tags BRouter routed over (`RouteCandidate.stretches`: runs of vertices
  of the route's geometry; neighbouring segments of the same class are merged). Each row of BRouter's
  tag table is placed on the geometry by its end point; if a point cannot be found the route is not
  coloured rather than coloured wrongly.
- **Not mapped** is a missing tag, which says nothing about the surface or the road. Traffic is the
  same estimate as the *Busy roads* number (road class, bike lanes, speed limits), not a count of cars,
  and it is "not mapped" everywhere when too little of the route carries road tags.
- **Gradient** needs elevation on every vertex (as the elevation chart does), so it works for any engine
  that returns it. It uses the chart's colours and the chart's grade -- taken over about 300 m around each
  point of the smoothed elevation, in 25 m steps -- so a short steep ramp inside a gentler climb does
  not flicker, and the map and the chart always agree. The legend leaves out bands of a few metres.
- Routes from engines that give no tags (ORS, Valhalla) have no Surface or Traffic mode. Classes of a few metres are left
  out of the legend, and a "longest stretch" under 300 m is not mentioned.

## Progress and where the time went

While a plan is made the status line says which step is running ("Routing... (step 5 of 11)"), and the
best route is drawn as a faint dashed line as soon as it exists -- before the forecast, the notes on the
alternatives and the files are done -- so a slow trip shows its line within seconds. When the plan arrives
it replaces that line. Under the route, *Where the time went* (collapsed; its summary reads like "9.0 s,
mostly routing") lists every step with its time and a bar relative to the slowest, which is highlighted.
A plan answered from the short-lived cache says so instead. The page uses `POST /v1/route/plan/stream`
([api.md](api.md#post-v1routeplanstream)); against an older server without it, it asks the plain way.
Starting another plan, or closing the page, cancels the one that is running.

## Directions

Under the elevation chart, *Directions* lists the route's turn instructions (collapsed; it says how
many turns there are): a start, each turn with the distance from the start, how far to the next one
("then 230 m"), and the arrival. Clicking a step shows where it is on the map. *Cue sheet (.txt)*
downloads the same list as plain text to print or send.

- With a **departure time** (so there is a forecast) every step also shows when you are expected there
  (`~10:42`, your local time, as on the weather card), and the cue sheet gets a time column. The time is
  the departure plus the step's share of the ride at an **even pace** over the whole route, so it is an
  estimate: climbs, stops, the wind and traffic lights all move it, and it uses the engine's duration,
  not the wind-adjusted one. Without a departure time there are no times.
- The instructions are the routing engine's (BRouter only; other engines show no list). BRouter knows
  no street names, so a step says "Turn left", not "onto Hauptstrasse".
- Steps are measured along the drawn route; a step the engine placed past the end of the route, or out
  of order, is left out instead of trusted.
- Not turned into GPX: see [scoring-and-exports.md](scoring-and-exports.md#geojson). The GeoJSON
  download does carry the cues.

## Remember my choices (opt-in)

The settings panel (the gear) has **Remember my choices**, **off until you switch it on**. When on, the
planner remembers what you tick, in this browser, and puts it back in the form on your next visit:

| Kind | What it holds | Ticked at first |
| --- | --- | --- |
| Bike type | the bike | yes |
| Surface preferences | prefer / avoid surfaces | yes |
| Busy roads and ferries | the two "avoid" boxes | yes |
| Distance and climbing limits | target distance, maximum distance, maximum ascent | no |
| Start place | the Origin field | no |

The defaults are a rider's habits, not the trip of the day; the limits and the start place are one tick
away for those who always ride "about 50 km from home". Never remembered: the destination, the stops, the
departure time.

- It saves as you fill in the form (a moment after you stop typing) and after a plan that worked, and
  nothing is saved while it is off. Unticking a kind forgets what it held at once; **Forget what is
  stored** empties it and keeps the switch; switching off erases everything it stored.
- **It stays in this browser** (`localStorage`, one entry). Nothing is sent to the server, not even
  that it is on. A private window or blocked storage just does not remember, and the panel says so.
- **A shared link always wins.** Opening a link (or pasting one into an open tab) fills the form from
  the link only, and the link's trip is *not* written over what you had remembered until you edit the form
  yourself: somebody else's trip does not become your habits.
- What is read back is checked like any text from outside (`frontend/memory.js`): only the known fields,
  of the right type and in range, reach the form; a value of a kind that is not ticked is ignored even if
  something stored it.
- This is a browser memory, not an account: another browser or device starts empty, and clearing the
  site's data clears it.

## Which route you download (the server owner's opt-in)

If whoever runs the server set `PICK_LOG_PATH`, downloading one of the compared routes also tells the server
which one -- numbers only (the routes' distance, surface shares, traffic, climbing, the recommended one's
position and the goals), no place, route shape or text -- and it is kept in a file on that machine
([optimizer.md](optimizer.md#what-people-pick)). Without the setting the page sends nothing.

## Share a trip with a link

**Copy link** (next to *Reset*) copies a link that opens the planner with this form's trip in it:
the places, via stops, bike type and constraints, the number of alternatives and, if it is still
ahead, the departure time. Opening such a link fills the form and plans the route straight away, so
it works for sending a trip to your phone or someone else. (On a page without clipboard access, for
example plain `http` beyond localhost, the link is shown in a box to copy by hand.)

- The trip is in the `#plan=...` **fragment** of the address, which browsers never send to a
  server: the places are not in any request log, and nothing is stored anywhere.
- A link is text from somewhere else, so it is read strictly (`frontend/link.js`): only the known
  fields, of the right type and in range, reach the form; a link without a usable origin and
  destination is ignored. A departure that has passed, or is more than 14 days ahead, is dropped.
- Not carried: the sights-on-the-way setting, the chosen route among the alternatives, and the
  optimiser's settings. A relative departure ("tomorrow morning") becomes the actual time it meant.

## Service status

A dot next to the title shows whether the services behind the planner are up -- green: all, amber: some
are down but plans still work, red: cannot plan (or the server itself cannot be reached). Click it for the
list: route engine, place search, weather, **sights**, route files (and history, cache when configured),
each with its state and the server's short reason (`HTTP 502`, `unreachable`). It reads `GET /readyz`
([api.md](api.md#get-readyz)) once a minute while the page is visible, and when you open the list. The
sight lookup uses free public Overpass servers that are often overloaded: when markers or stops do not
show up, this is where you see whether that is why. A server without `/readyz` shows no dot.

## Chat conversations

The chat panel keeps its conversations (when the server stores them, see [chat.md](chat.md)): **New chat**
starts a fresh one, **Earlier conversations** lists this browser's earlier ones to open again or delete.
A page that is reopened shows the conversation it had.

## Drag the route to reshape it

With a mouse, a round handle follows the pointer along the **active** route. Press it and drag it to
another place: the place you drop it becomes a **new stop**, inserted among your stops at the position
along the trip where you *grabbed* the route (not where you dropped it), and the trip is planned again
through it. It is the same as *Add & plan* in the menu below, without the menu.

- While you drag, a dashed line shows where the route will go: from the stop before, through the handle,
  to the stop after it (the places the new stop will sit between). A neighbour that is only a name has no
  position, so the line then runs from where you grabbed the route. It is a sketch of the intent, not the
  route: the real one is planned when you let go.
- A drag shorter than about 25 pixels is ignored (a slip, not a new stop). It needs a start and an end
  already, and room for another stop (10 at most); otherwise the page says so.
- The handle only follows the active route (pick another alternative first) and not while you are
  picking a point for a field.
- **Touch screen:** there is no hovering, so **press and hold the route** (a long press, within about
  28 pixels of it -- a finger does not land on a line): the handle appears on the nearest point of the
  route and stays; drag it, or tap anywhere else on the map to drop it (that tap only drops it,
  it does not pick a point). The page shows this hint on a touch device. Markers (A, B, the stops) are
  dragged directly. A mouse's right click is left to the browser (the long press is read from a plain DOM
  listener: a Leaflet `contextmenu` listener would cancel the browser's menu). Only the placement and the dismissal
  were exercised (with synthetic events); a real finger on a real phone was **not** tried.
- The click menu below also works by keyboard.
- Each drag of the route adds one stop. To **move a place you already have**, drag its marker (A, B or
  a numbered stop): the field takes the dropped point as `lat, lon` and the trip is planned again. This
  works with touch too, since markers are ordinary draggable pins; delete a stop in the form.

## Click a point, then choose where it goes

Click (or tap) any point on the map, including a point on a route that is drawn: a pin marks it and a
small menu shows its coordinates and **Add this point as**:

| Option | What it does |
| --- | --- |
| **Start (A)** | Makes it the start; the current start is replaced |
| **Stop 1 ... Stop N** | Inserts it as a via stop at that position: "after the start", "after stop 1" ..., the last one "before the end" |
| **End (B)** | Makes it the end; the current end is replaced |

The option that fits best is preselected:

- the first point you click becomes the **start**, the next one the **end**;
- with a start and an end, it goes in as the **stop** at the position it lies along the trip --
  along the route when one is on the map, otherwise along the straight lines between your places.
  A stop that is only a name (not a coordinate) cannot be placed, so the new one goes last.

**Add** puts the point in and tells you to press *Plan route*; **Add & plan** puts it in and plans at
once. **Cancel**, the close button, or clicking another point drops the selection. At most 10 stops
fit (the server's limit); then only start and end are offered. The stops are numbered on the map as
they are in the form, and the numbers follow when a new stop is inserted in the middle.

Notes:

- A click that only closes another popup (a sight, a weather marker) does not open the menu.
- Panning across the date line reports longitudes beyond 180; they are wrapped into -180..180.
- While a field is waiting for a map click (**Pick on map**), the click goes to that field, as before.
