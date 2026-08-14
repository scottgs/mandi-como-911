-- Schema for the `mandi_geo` database (owner role: mandi_geo), PostGIS-backed.
--
-- Deliberately contains no CREATE DATABASE / CREATE ROLE / CREATE OR REPLACE
-- statements -- those touch server-level objects and are handled by
-- install.sh's provision_db(), which only creates the role/database if they
-- don't already exist. Everything below is IF NOT EXISTS / additive only,
-- so re-running this file against a database that already has the schema is
-- always a safe no-op -- it will never drop or replace existing data.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS fire_medical_calls (
    in_num          BIGINT PRIMARY KEY,
    source_agency   TEXT,
    call_datetime   TIMESTAMPTZ NOT NULL,
    address         TEXT,
    apt_lot         TEXT,
    nature          TEXT,
    report_id       TEXT,
    patrol_area     TEXT,
    geox            NUMERIC,
    geoy            NUMERIC,
    geom            GEOGRAPHY(Point, 4326),
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fire_medical_calls_call_datetime_idx
    ON fire_medical_calls (call_datetime DESC);
CREATE INDEX IF NOT EXISTS fire_medical_calls_geom_idx
    ON fire_medical_calls USING gist (geom);

CREATE TABLE IF NOT EXISTS police_calls (
    in_num          BIGINT PRIMARY KEY,
    call_datetime   TIMESTAMPTZ NOT NULL,
    address         TEXT,
    nature          TEXT,
    report_id       TEXT,
    patrol_area     TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS police_calls_call_datetime_idx
    ON police_calls (call_datetime DESC);

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO mandi_geo;
