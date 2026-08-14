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

-- COMMENT ON is metadata-only (not a CREATE OR REPLACE, never touches data),
-- so it's safe to re-run alongside the rest of this idempotent file.
COMMENT ON TABLE fire_medical_calls IS
    'Columbia/Boone County Fire & Rescue dispatch calls, upserted by '
    'fetch/columbia-911-fire-fetch.py from como.gov''s fire_csvexport.php '
    'every 5 minutes. One row per in_num; a row is kept and updated forever '
    'once seen, not just for a rolling window -- the dashboard queries the '
    'last 24h out of this table, it doesn''t truncate it.';
COMMENT ON COLUMN fire_medical_calls.in_num IS
    'Source system''s unique incident number (como.gov CSV column "InNum"). Primary key.';
COMMENT ON COLUMN fire_medical_calls.source_agency IS
    'Responding agency code from the source feed (CSV column "Agency"), e.g. CFD/BCFD/SBFD.';
COMMENT ON COLUMN fire_medical_calls.call_datetime IS 'Dispatch call timestamp, tz-aware.';
COMMENT ON COLUMN fire_medical_calls.address IS 'Street address as reported by the source feed.';
COMMENT ON COLUMN fire_medical_calls.apt_lot IS
    'Apartment/lot number if present (CSV column "AptLot"), else NULL.';
COMMENT ON COLUMN fire_medical_calls.nature IS
    'Call nature/type text as reported, e.g. "Medical Response", "Fire Alarm" -- '
    'the dashboard color-codes this by keyword match (medical/fire/citizen/smoke).';
COMMENT ON COLUMN fire_medical_calls.report_id IS 'Source feed''s report identifier (CSV column "Report").';
COMMENT ON COLUMN fire_medical_calls.patrol_area IS
    'Source feed''s patrol/response area code for the address (CSV column "PolArea").';
COMMENT ON COLUMN fire_medical_calls.geox IS
    'Easting coordinate exactly as published by the source feed. Coordinate '
    'reference system: Missouri State Plane Central Zone, NAD83, US Survey '
    'Feet -- ESRI:102697. Raw/unconverted; see geom for the WGS84 version.';
COMMENT ON COLUMN fire_medical_calls.geoy IS
    'Northing coordinate exactly as published by the source feed. Same CRS '
    'as geox (ESRI:102697 -- Missouri State Plane Central, NAD83, US Survey Feet).';
COMMENT ON COLUMN fire_medical_calls.geom IS
    'geox/geoy transformed to WGS84 (EPSG:4326) via pyproj '
    '(Transformer.from_crs("ESRI:102697", "EPSG:4326", always_xy=True)) at '
    'fetch time. This is the column actually used for mapping/GIS queries; '
    'geox/geoy are kept alongside it only as the untransformed source values.';
COMMENT ON COLUMN fire_medical_calls.fetched_at IS
    'Timestamp of the fetch run that most recently upserted this row.';

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

COMMENT ON TABLE police_calls IS
    'Columbia Police Dispatch calls, upserted by '
    'fetch/columbia-911-police-fetch.py from como.gov''s police_csvexport.php '
    'every 30 minutes (vs. fire/medical''s 5 -- this feed is officially '
    '~6h-delayed per city policy, so faster polling has no benefit). No '
    'geox/geoy/geom here: the source feed carries no coordinates for police '
    'calls at all, unlike the fire/medical feed.';
COMMENT ON COLUMN police_calls.in_num IS
    'Source system''s unique incident number (como.gov CSV column "InNum"). Primary key.';
COMMENT ON COLUMN police_calls.call_datetime IS 'Dispatch call timestamp, tz-aware.';
COMMENT ON COLUMN police_calls.address IS
    'Block-level street address as reported by the source feed (e.g. "1400 BLOCK RANGE LINE ST"). '
    'Stored verbatim; the dashboard''s maps-link generation strips "BLOCK" only in the '
    'outbound query, not in this stored value.';
COMMENT ON COLUMN police_calls.nature IS 'Call type/nature text as reported.';
COMMENT ON COLUMN police_calls.report_id IS 'Source feed''s report identifier (CSV column "Report").';
COMMENT ON COLUMN police_calls.patrol_area IS
    'Source feed''s patrol area code for the address (CSV column "PolArea").';
COMMENT ON COLUMN police_calls.fetched_at IS
    'Timestamp of the fetch run that most recently upserted this row.';

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO mandi_geo;
