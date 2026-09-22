"""Score calibration against the curated benchmark set (issue #4).

The deterministic scorer's weights (``scoring/basic.py``) are reasonable
initial defaults, not calibrated values. This module is the measuring
instrument for revisiting them: it defines the benchmark schema, evaluates
candidates against the manually judged expectations of each case, and
re-ranks candidates under alternative weight choices.

It deliberately does not change how production scoring works --
``score_candidate`` stays the single source of truth for the graph. Two
principles from the enrichment data-quality policy carry over here:

* unknown stays unknown -- a check that lacks the data it needs is reported
  as *skipped*, never passed or failed;
* judged expectations are plausibility envelopes for a believable good
  route, not golden geometries; they must not be tightened to pin provider
  idiosyncrasies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bike_routing_agent.config import SURFACE_TAXONOMY
from bike_routing_agent.models import (
    Coordinate,
    RouteCandidate,
    RouteConstraints,
    RouteMetrics,
    RoutingRequest,
)
from bike_routing_agent.scoring.basic import (
    DISTANCE_WEIGHT,
    ELEVATION_WEIGHT,
    score_candidate,
)

# The surface-quality vocabulary RouteMetrics.surface_coverage keys share
# (config.SURFACE_TAXONOMY values); benchmark files may only reference these.
SURFACE_CATEGORIES = frozenset(SURFACE_TAXONOMY.values())

# Weight pairs the sensitivity report re-ranks under. The current shipped
# pair is first; the others bracket it so a report shows whether a ranking
# survives plausible alternative judgements.
DEFAULT_WEIGHT_GRID: tuple[tuple[float, float], ...] = (
    (0.65, 0.35),  # current defaults
    (0.50, 0.50),
    (0.80, 0.20),
    (0.35, 0.65),
)

DEFAULT_BENCHMARK_PATH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "core-v1.json"
)


class MetricRange(BaseModel):
    """Inclusive [min, max] envelope for one metric, in metres."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    minimum: float = Field(alias="min", ge=0)
    maximum: float = Field(alias="max", ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> MetricRange:
        if self.minimum > self.maximum:
            raise ValueError(f"empty range: min {self.minimum} > max {self.maximum}")
        return self


class CombinedShare(BaseModel):
    """Minimum/maximum combined coverage share for a set of categories."""

    model_config = ConfigDict(extra="forbid")

    categories: list[str] = Field(min_length=1)
    share: float = Field(ge=0, le=1)

    @field_validator("categories")
    @classmethod
    def _check_categories(cls, v: list[str]) -> list[str]:
        return _validated_categories(v)


class SurfaceExpectation(BaseModel):
    """Judged expectations over ``metrics.surface_coverage`` shares.

    Coverage shares are of the *known* route length (coverage + unknown = 1),
    so every check here is only meaningful when enrichment populated
    ``surface_coverage`` at all; without data every surface check skips.
    """

    model_config = ConfigDict(extra="forbid")

    min_by_category: dict[str, float] = Field(default_factory=dict)
    max_by_category: dict[str, float] = Field(default_factory=dict)
    min_combined: CombinedShare | None = None
    max_combined: CombinedShare | None = None
    # Lower bound on sum(surface_coverage.values()) -- how much of the route
    # is mapped at all. Distinct from max_unknown_surface_fraction: this one
    # reads the coverage dict itself, so it also catches a coverage dict that
    # is inconsistent with the reported unknown fraction.
    min_known_share: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _check_not_empty(self) -> SurfaceExpectation:
        if not (
            self.min_by_category
            or self.max_by_category
            or self.min_combined
            or self.max_combined
            or self.min_known_share is not None
        ):
            raise ValueError("an empty surface expectation judges nothing")
        return self

    @field_validator("min_by_category", "max_by_category")
    @classmethod
    def _check_shares(cls, v: dict[str, float]) -> dict[str, float]:
        _validated_categories(list(v))
        for category, share in v.items():
            if not 0.0 <= share <= 1.0:
                raise ValueError(f"share for {category!r} outside [0, 1]: {share}")
        return v


class Expectations(BaseModel):
    """Manually judged characteristics a good route for the case should show."""

    model_config = ConfigDict(extra="forbid")

    distance_m: MetricRange | None = None
    ascent_m: MetricRange | None = None
    max_unknown_surface_fraction: float | None = Field(default=None, ge=0, le=1)
    surface: SurfaceExpectation | None = None

    @model_validator(mode="after")
    def _check_not_empty(self) -> Expectations:
        if not (
            self.distance_m
            or self.ascent_m
            or self.max_unknown_surface_fraction is not None
            or self.surface
        ):
            raise ValueError("a case with no expectations judges nothing")
        return self


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Coordinate
    destination: Coordinate
    via: list[Coordinate] = Field(default_factory=list)
    constraints: RouteConstraints = Field(default_factory=RouteConstraints)


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    case_id: str = Field(alias="id", min_length=1)
    title: str = Field(min_length=1)
    request: BenchmarkRequest
    expectations: Expectations
    rationale: str = Field(min_length=1)


class BenchmarkSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    region: str = Field(min_length=1)
    judged_by: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)
    cases: list[BenchmarkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> BenchmarkSet:
        ids = [c.case_id for c in self.cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {duplicates}")
        return self


def _validated_categories(categories: list[str]) -> list[str]:
    unknown = sorted(set(categories) - SURFACE_CATEGORIES)
    if unknown:
        raise ValueError(
            f"unknown surface categories {unknown}; "
            f"valid: {sorted(SURFACE_CATEGORIES)}"
        )
    return categories


def load_benchmark(path: Path | str = DEFAULT_BENCHMARK_PATH) -> BenchmarkSet:
    """Load and validate a benchmark set from JSON."""
    with open(path, encoding="utf-8") as fh:
        return BenchmarkSet.model_validate(json.load(fh))


def build_request(case: BenchmarkCase) -> RoutingRequest:
    """The case's routing request, identical to what a live run would send."""
    return RoutingRequest(
        origin=case.request.origin,
        destination=case.request.destination,
        via=list(case.request.via),
        constraints=case.request.constraints,
    )


CheckStatus = Literal["passed", "failed", "skipped"]


class CaseCheck(BaseModel):
    """One expectation checked against one candidate."""

    name: str
    status: CheckStatus
    detail: str


class CaseEvaluation(BaseModel):
    case_id: str
    provider: str
    checks: list[CaseCheck]

    @property
    def failed(self) -> list[CaseCheck]:
        return [c for c in self.checks if c.status == "failed"]

    @property
    def skipped(self) -> list[CaseCheck]:
        return [c for c in self.checks if c.status == "skipped"]

    @property
    def passed(self) -> bool:
        """True unless a check actively contradicted the judgement.

        Skipped checks never fail the evaluation: missing data is reported,
        not punished (the enrichment data-quality policy, carried over).
        """
        return not self.failed


def evaluate_candidate(case: BenchmarkCase, candidate: RouteCandidate) -> CaseEvaluation:
    """Judge one candidate against one case's expectations."""
    checks: list[CaseCheck] = []
    metrics = candidate.metrics
    exp = case.expectations

    if exp.distance_m:
        checks.append(
            _range_check("distance_m", metrics.distance_m, exp.distance_m, "m")
        )
    if exp.ascent_m:
        checks.append(_range_check("ascent_m", metrics.ascent_m, exp.ascent_m, "m"))
    if exp.max_unknown_surface_fraction is not None:
        checks.append(_unknown_fraction_check(metrics, exp))
    if exp.surface:
        checks.extend(_surface_checks(metrics, exp.surface))

    return CaseEvaluation(case_id=case.case_id, provider=candidate.provider, checks=checks)


def _range_check(
    name: str, value: float | None, envelope: MetricRange, unit: str
) -> CaseCheck:
    if value is None:
        return CaseCheck(
            name=name, status="skipped", detail=f"no {name} data to judge"
        )
    ok = envelope.minimum <= value <= envelope.maximum
    return CaseCheck(
        name=name,
        status="passed" if ok else "failed",
        detail=f"{name}={value:.0f}{unit} expected [{envelope.minimum:.0f}, "
        f"{envelope.maximum:.0f}]{unit}",
    )


def _unknown_fraction_check(metrics: RouteMetrics, exp: Expectations) -> CaseCheck:
    fraction = metrics.unknown_surface_fraction
    if fraction is None:
        return CaseCheck(
            name="unknown_surface_fraction",
            status="skipped",
            detail="no surface enrichment data to judge",
        )
    limit = exp.max_unknown_surface_fraction
    assert limit is not None  # set by the caller branch
    return CaseCheck(
        name="unknown_surface_fraction",
        status="passed" if fraction <= limit + 1e-9 else "failed",
        detail=f"unknown={fraction:.2f} expected <= {limit:.2f}",
    )


def _surface_checks(
    metrics: RouteMetrics, exp: SurfaceExpectation
) -> list[CaseCheck]:
    coverage = metrics.surface_coverage
    if not coverage:
        # Without enrichment the shares are simply unknown; recording the
        # judgement as skipped keeps a disabled enricher from failing the
        # benchmark while still surfacing which judgements went untested.
        return [
            CaseCheck(
                name="surface",
                status="skipped",
                detail="no surface enrichment data to judge",
            )
        ]
    checks: list[CaseCheck] = []
    for category, limit in sorted(exp.min_by_category.items()):
        observed = coverage.get(category, 0.0)
        checks.append(
            _share_check(
                f"surface.{category}.min", observed, "greater-or-equal", limit
            )
        )
    for category, limit in sorted(exp.max_by_category.items()):
        observed = coverage.get(category, 0.0)
        checks.append(
            _share_check(
                f"surface.{category}.max", observed, "less-or-equal", limit
            )
        )
    for label, combined in (
        ("min_combined", exp.min_combined),
        ("max_combined", exp.max_combined),
    ):
        if combined is None:
            continue
        observed = sum(coverage.get(c, 0.0) for c in combined.categories)
        checks.append(
            _share_check(
                f"surface.{label}",
                observed,
                "greater-or-equal" if label == "min_combined" else "less-or-equal",
                combined.share,
                f"{'/'.join(combined.categories)}",
            )
        )
    if exp.min_known_share is not None:
        checks.append(
            _share_check(
                "surface.known_share",
                sum(coverage.values()),
                "greater-or-equal",
                exp.min_known_share,
            )
        )
    return checks


def _share_check(
    name: str, observed: float, relation: str, limit: float, subject: str = ""
) -> CaseCheck:
    ok = observed >= limit - 1e-9 if relation == "greater-or-equal" else (
        observed <= limit + 1e-9
    )
    return CaseCheck(
        name=name,
        status="passed" if ok else "failed",
        detail=f"{subject or 'share'}={observed:.2f} expected {relation} "
        f"{limit:.2f}",
    )


def ranked_candidates(
    candidates: list[RouteCandidate],
    constraints: RouteConstraints,
    distance_weight: float = DISTANCE_WEIGHT,
    elevation_weight: float = ELEVATION_WEIGHT,
) -> list[RouteCandidate]:
    """Score candidates and return them best-first, optionally re-weighted.

    Components come from ``score_candidate``'s breakdown, so the re-weighted
    view can never disagree with the production scorer about the components
    themselves -- only about how they trade off. Weights need not sum to 1;
    the ranking is invariant to their common scale.
    """
    scored: list[RouteCandidate] = []
    for candidate in candidates:
        _, breakdown = score_candidate(candidate, constraints)
        score = (
            distance_weight * breakdown["distance_fit"]
            + elevation_weight * breakdown["elevation_fit"]
            - breakdown["warning_penalty"]
        )
        scored.append(
            candidate.model_copy(
                update={"score": score, "score_breakdown": breakdown}
            )
        )
    scored.sort(key=lambda c: (-float(c.score or 0.0), c.provider))
    return scored


def weight_sensitivity(
    candidates: list[RouteCandidate],
    constraints: RouteConstraints,
    grid: tuple[tuple[float, float], ...] = DEFAULT_WEIGHT_GRID,
) -> dict[str, list[str]]:
    """Provider ranking per weight pair, for the calibration report.

    A ranking that flips inside the plausible weight region is the signal
    the scorer is not robust for that case -- worth more benchmark cases,
    not a retuned constant.
    """
    report: dict[str, list[str]] = {}
    for w_distance, w_elevation in grid:
        label = f"{w_distance:.2f}/{w_elevation:.2f}"
        ranked = ranked_candidates(candidates, constraints, w_distance, w_elevation)
        report[label] = [c.provider for c in ranked]
    return report


def evaluation_rows(
    case: BenchmarkCase, candidates: list[RouteCandidate]
) -> list[dict[str, Any]]:
    """Flat per-candidate verdict rows, convenient for report writing."""
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        evaluation = evaluate_candidate(case, candidate)
        rows.append(
            {
                "case": case.case_id,
                "provider": candidate.provider,
                "score": candidate.score,
                "passed": evaluation.passed,
                "failed": [c.name for c in evaluation.failed],
                "skipped": [c.name for c in evaluation.skipped],
                "details": {c.name: c.detail for c in evaluation.checks},
            }
        )
    return rows
