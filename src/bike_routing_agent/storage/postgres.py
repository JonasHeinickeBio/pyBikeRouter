"""PostGIS-backed route history and artifact storage (issue #7).

``psycopg`` is an optional dependency (the ``db`` extra) and is imported
lazily: a deployment without a database never needs it. One
:class:`PostgresDatabase` owns the connection pool and creates the schema on
first use; :class:`PostgresRouteHistory` and :class:`PostgresArtifactStore`
are thin views over it. All calls are synchronous -- graph nodes already run
in worker threads and the API wraps history calls in ``run_in_threadpool``.

Schema notes:

* ``plans`` -- one row per answered request (any status), with the request as
  received and the resolved endpoints as ``geometry(Point, 4326)``.
* ``candidates`` -- one row per scored candidate of a ``ready`` plan. The
  full candidate JSON (lossless, including elevations) is the source of
  truth; ``geom`` is a 2D ``LineString`` copy that exists to be spatially
  indexed, and the scalar columns exist to be filtered and aggregated
  (calibration/dashboards read these).
* ``artifacts`` -- exported GeoJSON/GPX content, so API instances share them.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from bike_routing_agent.models import Coordinate
from bike_routing_agent.storage.history import (
    CandidateSummary,
    DailyStats,
    HistoryStats,
    PlanFilter,
    PlanRecord,
    PlanSummary,
    ProviderStats,
    StatsFilter,
    StoredCandidate,
)

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS plans (
    plan_id      text PRIMARY KEY,
    created_at   timestamptz NOT NULL,
    status       text NOT NULL,
    bike_type    text,
    request      jsonb NOT NULL,
    constraints  jsonb NOT NULL,
    origin       geometry(Point, 4326),
    destination  geometry(Point, 4326),
    errors       jsonb NOT NULL DEFAULT '[]',
    explanation  text,
    artifacts    jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS plans_created_at_idx ON plans (created_at DESC);
CREATE INDEX IF NOT EXISTS plans_status_idx ON plans (status);

CREATE TABLE IF NOT EXISTS candidates (
    plan_id           text NOT NULL REFERENCES plans (plan_id) ON DELETE CASCADE,
    rank              integer NOT NULL,
    selected          boolean NOT NULL,
    provider          text NOT NULL,
    provider_profile  text NOT NULL,
    score             double precision,
    distance_m        double precision NOT NULL,
    duration_s        double precision,
    ascent_m          double precision,
    descent_m         double precision,
    score_breakdown   jsonb NOT NULL DEFAULT '{}',
    warnings          jsonb NOT NULL DEFAULT '[]',
    provenance        jsonb NOT NULL DEFAULT '{}',
    candidate         jsonb NOT NULL,
    geom              geometry(LineString, 4326),
    PRIMARY KEY (plan_id, rank)
);
CREATE INDEX IF NOT EXISTS candidates_provider_idx ON candidates (provider, provider_profile);
CREATE INDEX IF NOT EXISTS candidates_geom_idx ON candidates USING gist (geom);

CREATE TABLE IF NOT EXISTS artifacts (
    name        text PRIMARY KEY,
    content     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""

# Candidate geometry -> 2D LineString. ST_Force2D drops elevations (the
# jsonb keeps them); ST_LineMerge flattens a MultiLineString when its parts
# connect, and a geometry that still is not a single line is stored as NULL
# rather than failing the whole save (it stays in the jsonb).
_CANDIDATE_GEOM_SQL = """
    CASE WHEN %(geometry)s::text IS NULL THEN NULL ELSE (
        SELECT CASE WHEN GeometryType(g) = 'LINESTRING' THEN g END
        FROM (
            SELECT ST_LineMerge(
                ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%(geometry)s::text), 4326))
            ) AS g
        ) AS merged
    ) END
"""


def _import_psycopg() -> Any:
    try:
        import psycopg
        import psycopg_pool
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "DATABASE_URL is set but psycopg is not installed; "
            "install the database extra: pip install 'bike-routing-agent[db]'"
        ) from exc
    return psycopg, psycopg_pool


class PostgresDatabase:
    """Lazily opened connection pool plus one-time schema creation."""

    def __init__(self, url: str, *, max_size: int = 5) -> None:
        self._url = url
        self._max_size = max_size
        self._lock = threading.Lock()
        self._pool: Any = None

    def connection(self) -> Any:
        """A pooled connection context manager (commits on clean exit)."""
        return self._ensure_pool().connection()

    def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is None:
                _, psycopg_pool = _import_psycopg()
                pool = psycopg_pool.ConnectionPool(
                    self._url, min_size=1, max_size=self._max_size, open=False
                )
                pool.open(wait=True)
                with pool.connection() as conn:
                    conn.execute(SCHEMA_SQL)
                self._pool = pool
        return self._pool

    def close(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None


def _plan_conditions(
    params: dict[str, Any],
    *,
    status: str | None = None,
    bike_type: str | None = None,
    since: Any = None,
    until: Any = None,
) -> list[str]:
    """WHERE fragments over ``plans p`` shared by queries and aggregates."""
    where: list[str] = []
    if status is not None:
        where.append("p.status = %(status)s")
        params["status"] = status
    if bike_type is not None:
        where.append("p.bike_type = %(bike_type)s")
        params["bike_type"] = bike_type
    if since is not None:
        where.append("p.created_at >= %(since)s")
        params["since"] = since
    if until is not None:
        where.append("p.created_at < %(until)s")
        params["until"] = until
    return where


def _where(conditions: list[str]) -> str:
    return " WHERE " + " AND ".join(conditions) if conditions else ""


def _point_sql(name: str) -> str:
    return (
        f"CASE WHEN %({name}_lon)s::float8 IS NULL THEN NULL "
        f"ELSE ST_SetSRID(ST_MakePoint(%({name}_lon)s::float8, %({name}_lat)s::float8), 4326) END"
    )


class PostgresRouteHistory:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    def save(self, record: PlanRecord) -> None:
        from psycopg.types.json import Jsonb

        with self._db.connection() as conn:
            conn.execute("DELETE FROM plans WHERE plan_id = %s", (record.plan_id,))
            conn.execute(
                f"""
                INSERT INTO plans (plan_id, created_at, status, bike_type, request, constraints,
                                   origin, destination, errors, explanation, artifacts)
                VALUES (%(plan_id)s, %(created_at)s, %(status)s, %(bike_type)s, %(request)s,
                        %(constraints)s, {_point_sql("origin")}, {_point_sql("destination")},
                        %(errors)s, %(explanation)s, %(artifacts)s)
                """,
                {
                    "plan_id": record.plan_id,
                    "created_at": record.created_at,
                    "status": record.status,
                    "bike_type": record.bike_type,
                    "request": Jsonb(record.request),
                    "constraints": Jsonb(record.constraints),
                    "origin_lon": record.origin.lon if record.origin else None,
                    "origin_lat": record.origin.lat if record.origin else None,
                    "destination_lon": record.destination.lon if record.destination else None,
                    "destination_lat": record.destination.lat if record.destination else None,
                    "errors": Jsonb(record.errors),
                    "explanation": record.explanation,
                    "artifacts": Jsonb(record.artifacts),
                },
            )
            for stored in record.candidates:
                c = stored.candidate
                conn.execute(
                    f"""
                    INSERT INTO candidates (plan_id, rank, selected, provider, provider_profile,
                                            score, distance_m, duration_s, ascent_m, descent_m,
                                            score_breakdown, warnings, provenance, candidate, geom)
                    VALUES (%(plan_id)s, %(rank)s, %(selected)s, %(provider)s, %(profile)s,
                            %(score)s, %(distance_m)s, %(duration_s)s, %(ascent_m)s,
                            %(descent_m)s, %(breakdown)s, %(warnings)s, %(provenance)s,
                            %(candidate)s, {_CANDIDATE_GEOM_SQL})
                    """,
                    {
                        "plan_id": record.plan_id,
                        "rank": stored.rank,
                        "selected": stored.selected,
                        "provider": c.provider,
                        "profile": c.provider_profile,
                        "score": c.score,
                        "distance_m": c.metrics.distance_m,
                        "duration_s": c.metrics.duration_s,
                        "ascent_m": c.metrics.ascent_m,
                        "descent_m": c.metrics.descent_m,
                        "breakdown": Jsonb(c.score_breakdown),
                        "warnings": Jsonb(c.warnings),
                        "provenance": Jsonb(c.provenance),
                        "candidate": Jsonb(c.model_dump(mode="json")),
                        "geometry": json.dumps(c.geometry_geojson),
                    },
                )

    def get(self, plan_id: str) -> PlanRecord | None:
        with self._db.connection() as conn:
            plan = conn.execute(
                """
                SELECT plan_id, created_at, status, request, constraints,
                       ST_X(origin), ST_Y(origin), ST_X(destination), ST_Y(destination),
                       errors, explanation, artifacts
                FROM plans WHERE plan_id = %s
                """,
                (plan_id,),
            ).fetchone()
            if plan is None:
                return None
            rows = conn.execute(
                "SELECT rank, selected, candidate FROM candidates WHERE plan_id = %s ORDER BY rank",
                (plan_id,),
            ).fetchall()
        (pid, created_at, status, request, constraints, o_lon, o_lat, d_lon, d_lat) = plan[:9]
        errors, explanation, artifacts = plan[9:]
        return PlanRecord(
            plan_id=pid,
            created_at=created_at,
            status=status,
            request=request,
            constraints=constraints,
            origin=Coordinate(lon=o_lon, lat=o_lat) if o_lon is not None else None,
            destination=Coordinate(lon=d_lon, lat=d_lat) if d_lon is not None else None,
            errors=errors,
            explanation=explanation,
            artifacts=artifacts,
            candidates=[
                StoredCandidate(candidate=candidate, selected=selected, rank=rank)
                for rank, selected, candidate in rows
            ],
        )

    def query(self, plan_filter: PlanFilter) -> list[PlanSummary]:
        params: dict[str, Any] = {}
        where = _plan_conditions(
            params,
            status=plan_filter.status,
            bike_type=plan_filter.bike_type,
            since=plan_filter.since,
            until=plan_filter.until,
        )

        cand_where: list[str] = []
        if plan_filter.selected_only:
            cand_where.append("c.selected")
        if plan_filter.provider is not None:
            cand_where.append("c.provider = %(provider)s")
            params["provider"] = plan_filter.provider
        if plan_filter.profile is not None:
            cand_where.append("c.provider_profile = %(profile)s")
            params["profile"] = plan_filter.profile
        if plan_filter.bbox is not None:
            cand_where.append("c.geom && ST_MakeEnvelope(%(x0)s, %(y0)s, %(x1)s, %(y1)s, 4326)")
            params.update(
                zip(("x0", "y0", "x1", "y1"), plan_filter.bbox, strict=True)
            )
        if cand_where:
            where.append(
                "EXISTS (SELECT 1 FROM candidates c WHERE c.plan_id = p.plan_id AND "
                + " AND ".join(cand_where)
                + ")"
            )

        sql = (
            "SELECT p.plan_id, p.created_at, p.status, p.bike_type, "
            "ST_X(p.origin), ST_Y(p.origin), ST_X(p.destination), ST_Y(p.destination) "
            "FROM plans p"
            + _where(where)
            + " ORDER BY p.created_at DESC, p.plan_id LIMIT %(limit)s OFFSET %(offset)s"
        )
        params["limit"] = plan_filter.limit
        params["offset"] = plan_filter.offset

        with self._db.connection() as conn:
            plans = conn.execute(sql, params).fetchall()
            by_plan: dict[str, list[CandidateSummary]] = {p[0]: [] for p in plans}
            if by_plan:
                cands = conn.execute(
                    """
                    SELECT plan_id, provider, provider_profile, score, distance_m, duration_s,
                           ascent_m, selected, rank
                    FROM candidates WHERE plan_id = ANY(%s) ORDER BY plan_id, rank
                    """,
                    (list(by_plan),),
                ).fetchall()
                for pid, prov, prof, score, dist, dur, asc, sel, rank in cands:
                    by_plan[pid].append(
                        CandidateSummary(
                            provider=prov,
                            provider_profile=prof,
                            score=score,
                            distance_m=dist,
                            duration_s=dur,
                            ascent_m=asc,
                            selected=sel,
                            rank=rank,
                        )
                    )
        return [
            PlanSummary(
                plan_id=pid,
                created_at=created_at,
                status=status,
                bike_type=bike_type,
                origin=Coordinate(lon=o_lon, lat=o_lat) if o_lon is not None else None,
                destination=Coordinate(lon=d_lon, lat=d_lat) if d_lon is not None else None,
                candidates=by_plan[pid],
            )
            for pid, created_at, status, bike_type, o_lon, o_lat, d_lon, d_lat in plans
        ]


    def stats(self, stats_filter: StatsFilter) -> HistoryStats:
        params: dict[str, Any] = {}
        where = _where(
            _plan_conditions(
                params,
                bike_type=stats_filter.bike_type,
                since=stats_filter.since,
                until=stats_filter.until,
            )
        )
        with self._db.connection() as conn:
            daily_rows = conn.execute(
                "SELECT (p.created_at AT TIME ZONE 'UTC')::date, p.status, count(*) "
                f"FROM plans p{where} GROUP BY 1, 2 ORDER BY 1, 2",
                params,
            ).fetchall()
            engine_rows = conn.execute(
                "SELECT c.provider, c.provider_profile, count(*), "
                "count(*) FILTER (WHERE c.selected), avg(c.score), avg(c.distance_m), "
                "avg(c.duration_s), avg(c.ascent_m) "
                f"FROM candidates c JOIN plans p USING (plan_id){where} "
                "GROUP BY 1, 2 ORDER BY 1, 2",
                params,
            ).fetchall()
            breakdown_rows = conn.execute(
                "SELECT c.provider, c.provider_profile, kv.key, avg(kv.value::float8) "
                "FROM candidates c JOIN plans p USING (plan_id), "
                f"LATERAL jsonb_each_text(c.score_breakdown) AS kv{where} "
                "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
                params,
            ).fetchall()

        breakdowns: dict[tuple[str, str], dict[str, float]] = {}
        for provider, profile, name, mean in breakdown_rows:
            breakdowns.setdefault((provider, profile), {})[name] = mean

        by_status: dict[str, int] = {}
        daily: dict[Any, dict[str, int]] = {}
        for day, status, count in daily_rows:
            by_status[status] = by_status.get(status, 0) + count
            daily.setdefault(day, {})[status] = count
        total = sum(by_status.values())
        return HistoryStats(
            total_plans=total,
            by_status=by_status,
            ready_rate=by_status.get("ready", 0) / total if total else None,
            providers=[
                ProviderStats(
                    provider=provider,
                    provider_profile=profile,
                    candidates=n,
                    selected=selected,
                    win_rate=selected / n,
                    mean_score=score,
                    mean_distance_m=distance,
                    mean_duration_s=duration,
                    mean_ascent_m=ascent,
                    mean_score_breakdown=breakdowns.get((provider, profile), {}),
                )
                for provider, profile, n, selected, score, distance, duration, ascent in engine_rows
            ],
            daily=[
                DailyStats(date=day, total=sum(counts.values()), by_status=counts)
                for day, counts in daily.items()
            ],
        )


class PostgresArtifactStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    def put(self, name: str, content: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (name, content) VALUES (%s, %s)
                ON CONFLICT (name) DO UPDATE SET content = EXCLUDED.content, created_at = now()
                """,
                (name, content),
            )

    def get(self, name: str) -> bytes | None:
        with self._db.connection() as conn:
            row = conn.execute("SELECT content FROM artifacts WHERE name = %s", (name,)).fetchone()
        return None if row is None else str(row[0]).encode()
