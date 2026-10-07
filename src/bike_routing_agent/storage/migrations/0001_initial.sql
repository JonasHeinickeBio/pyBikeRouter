-- 0001: initial schema (issue #7): route history (plans, candidates) and the
-- database artifact store. Every statement is idempotent on purpose: databases
-- created before migrations existed already contain these objects, and this
-- migration simply records them as version 1.
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
