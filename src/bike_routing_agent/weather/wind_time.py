"""How much the wind changes the travel time (an estimate, not a measurement).

The engine's duration assumes still air. For a rider holding the *same power* as that duration
implies, the standard cycling power equation says what the forecast wind does to the speed::

    P = (F_rolling + F_climb + F_air) * v      F_air = 1/2 * rho * CdA * (v + w) * |v + w|

with ``v`` the ground speed, ``w`` the headwind (negative = tailwind), ``F_rolling = Crr * m * g``
and ``F_climb = m * g * (ascent / distance)``. The power is that of the engine's still-air pace
(``w = 0``); the speed in the wind is the one that needs the same power, found per stretch of the
route from the headwind at the forecast points. The result is a *factor on the engine's duration*,
so everything else the engine knows (surfaces, climbs) stays in the number.

What it is and is not:

* the same-power assumption is the textbook one; real riders push harder into a headwind and ease
  off with a tailwind, so the estimate is on the pessimistic side for headwinds and on the
  optimistic side for tailwinds;
* the rider (mass, rolling resistance, frontal area) is a typical one per bike type; the frontal
  area is the least certain, so the range returned varies it by +-25 %;
* only the wind *along* the direction of travel counts (a crosswind is ignored), gusts are ignored,
  and the forecast points are kilometres apart;
* not validated against measured rides. It is information for planning, never a statement about
  safety, and it does not change the plan or the ranking.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

AIR_DENSITY_KG_M3 = 1.2
GRAVITY_M_S2 = 9.81
KMH = 1 / 3.6
# The frontal area is the least certain input: the range shows what +-25 % of it does.
CDA_SPREAD = 0.25
# Speeds outside this are not what a rider does on a bike (a stalled rider, a motorbike).
MIN_SPEED_M_S = 1.0
MAX_SPEED_M_S = 20.0


@dataclass(frozen=True)
class Rider:
    """Rider and bike together: ``mass_kg`` with bike and load, rolling resistance, CdA (m^2)."""

    mass_kg: float
    crr: float
    cda: float


# Typical values per bike type (the app's BikeType values). The e-bike is left out on purpose: the
# motor's assistance changes with speed, so "the same power" means nothing there.
RIDERS: dict[str, Rider] = {
    "road": Rider(85.0, 0.0045, 0.36),
    "gravel": Rider(88.0, 0.0070, 0.42),
    "touring": Rider(100.0, 0.0060, 0.55),  # loaded, upright
    "mountain": Rider(88.0, 0.0100, 0.50),
    "city": Rider(95.0, 0.0060, 0.58),  # upright
    "commuter": Rider(88.0, 0.0050, 0.45),
    "recumbent": Rider(95.0, 0.0050, 0.30),
}


def rider_for(bike_type: str) -> Rider | None:
    """The typical rider for a bike type, or ``None`` when the model does not apply (e-bikes)."""
    return RIDERS.get(bike_type)


@dataclass(frozen=True)
class WindTime:
    """The wind's effect on one route's travel time."""

    still_air_s: float  # the engine's duration
    adjusted_s: float  # with the wind
    effect_s: float  # adjusted - still air: positive = slower
    low_s: float  # the effect with a 25 % smaller frontal area
    high_s: float  # ... and a 25 % larger one

    @property
    def effect_share(self) -> float:
        return self.effect_s / self.still_air_s if self.still_air_s > 0 else 0.0


def _power_w(v: float, headwind: float, force_other: float, cda: float) -> float:
    air = 0.5 * AIR_DENSITY_KG_M3 * cda * (v + headwind) * abs(v + headwind)
    return (force_other + air) * v


def speed_in_wind(v0: float, headwind: float, rider: Rider, grade: float = 0.0) -> float:
    """Ground speed (m/s) that needs the power of riding at ``v0`` in still air, in a headwind
    of ``headwind`` m/s (negative = tailwind), on a mean climb of ``grade`` (a fraction)."""
    return _speed_in_wind(v0, headwind, rider.mass_kg, rider.crr, rider.cda, grade)


def _speed_in_wind(
    v0: float, headwind: float, mass: float, crr: float, cda: float, grade: float
) -> float:
    force_other = mass * GRAVITY_M_S2 * (crr + max(grade, 0.0))
    target = _power_w(v0, 0.0, force_other, cda)
    if headwind == 0.0 or target <= 0.0:
        return v0

    def excess(v: float) -> float:
        return _power_w(v, headwind, force_other, cda) - target

    if excess(MAX_SPEED_M_S) <= 0:
        return MAX_SPEED_M_S
    # Once the air is against the rider (v + w >= 0) the power needed rises steadily with speed, so
    # the speed that needs exactly the target power is found by bisection. With a tailwind the
    # relevant branch starts where the rider moves as fast as the wind (v = -w): below that the
    # wind pushes and the power curve is not monotonic (a rider slower than the wind -- only with
    # a strong tailwind and a heavy bike -- is read as simply not slower than that).
    low = max(MIN_SPEED_M_S, -headwind)
    if excess(low) >= 0:
        return low
    high = MAX_SPEED_M_S
    for _ in range(40):
        mid = (low + high) / 2
        if excess(mid) > 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def _factor(
    fractions: Sequence[float],
    headwinds_kmh: Sequence[float],
    v0: float,
    rider_args: tuple[float, float, float],
    grade: float,
) -> float:
    """Duration factor (>1 = slower) over the stretches between the forecast points."""
    mass, crr, cda = rider_args
    breakpoints = [0.0, *fractions, 1.0]
    winds = [headwinds_kmh[0], *headwinds_kmh, headwinds_kmh[-1]]  # the ends take the nearest point
    total = 0.0
    for i in range(len(breakpoints) - 1):
        share = breakpoints[i + 1] - breakpoints[i]
        if share <= 0:
            continue
        wind = (winds[i] + winds[i + 1]) / 2 * KMH
        total += share * v0 / _speed_in_wind(v0, wind, mass, crr, cda, grade)
    return total


def wind_time(
    *,
    distance_m: float,
    duration_s: float,
    ascent_m: float | None,
    points: Sequence[tuple[float, float | None]],
    rider: Rider,
) -> WindTime | None:
    """The wind's effect on the travel time of a route, or ``None`` when it cannot be judged.

    ``points`` are ``(fraction along the route, headwind km/h)`` at the forecast points, in route
    order (a point without a headwind is skipped). ``None`` without distance, duration or any
    headwind, or when the pace is outside what the model describes.
    """
    known = [(f, w) for f, w in points if w is not None]
    if distance_m <= 0 or duration_s <= 0 or not known:
        return None
    v0 = distance_m / duration_s
    if not MIN_SPEED_M_S <= v0 <= MAX_SPEED_M_S:
        return None
    fractions = [f for f, _ in known]
    winds = [float(w) for _, w in known]
    grade = (ascent_m or 0.0) / distance_m

    def effect(cda_scale: float) -> float:
        args = (rider.mass_kg, rider.crr, rider.cda * cda_scale)
        return duration_s * (_factor(fractions, winds, v0, args, grade) - 1.0)

    central = effect(1.0)
    spread = sorted((effect(1.0 - CDA_SPREAD), effect(1.0 + CDA_SPREAD)))
    return WindTime(
        still_air_s=duration_s,
        adjusted_s=duration_s + central,
        effect_s=central,
        low_s=min(spread[0], central),
        high_s=max(spread[1], central),
    )


def describe(effect: WindTime) -> str | None:
    """ "With this wind the ride takes about 12 min longer (9-15 min) ..." when it is worth
    stating, else ``None``: from 3 minutes or 3 % of the ride, whichever is larger (smaller is
    below what a forecast several kilometres apart can support)."""
    if abs(effect.effect_s) < max(180.0, 0.03 * effect.still_air_s):
        return None
    a, b = sorted(round(abs(x) / 60) for x in (effect.low_s, effect.high_s))
    span = f" ({a}-{b} min)" if a != b else ""
    centre = round(abs(effect.effect_s) / 60)
    word = "longer" if effect.effect_s > 0 else "less"
    return (
        f"With this wind the ride takes about {centre} min {word}{span} than the engine's "
        "still-air time (an estimate, for the same effort as in still air)."
    )
