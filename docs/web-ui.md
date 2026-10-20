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

## Chat conversations

The chat panel keeps its conversations (when the server stores them, see [chat.md](chat.md)): **New chat**
starts a fresh one, **Earlier conversations** lists this browser's earlier ones to open again or delete.
A page that is reopened shows the conversation it had.

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
