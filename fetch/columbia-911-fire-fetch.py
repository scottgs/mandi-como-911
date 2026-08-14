#!/usr/bin/env python3
"""Fetch Columbia/Boone County Fire & Rescue dispatch calls from como.gov's
CSV export, transform each call's Missouri State Plane coordinates to
WGS84, upsert every row into the mandi_geo PostgreSQL/PostGIS database
(table `fire_medical_calls`), then query the last rolling 24h back out of
the DB and atomically rebuild the JSON cache Home Assistant reads
($HA_WWW_DIR/columbia_911/fire_medical.json).

Design/rationale: docs/tender-wibbling-treehouse.md
Source research: geox/geoy are Missouri State Plane Central Zone (NAD83, US
Survey Feet -- ESRI:102697); the police feed (police_csvexport.php) has no
coordinates at all, so it isn't handled by this script.

Run standalone to test: python3 columbia-911-fire-fetch.py
Runs on a schedule via the columbia-911-fire-fetch.timer systemd unit.
"""
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from pyproj import Transformer

URL = "https://www.como.gov/CMS/911dispatch/fire_csvexport.php"
TZ = ZoneInfo("America/Chicago")
CACHE_PATH = os.path.join(
    os.path.expanduser(os.environ.get("HA_WWW_DIR", "~/homeassistant/config/www")),
    "columbia_911", "fire_medical.json",
)

# como.gov's WAF blocks automation-signature User-Agents (confirmed: a
# Playwright browser session got an "Attack ID" block page here even though
# a plain curl request with a normal browser UA got a clean 200 from the
# same IP) -- use a real browser UA, not a self-identifying bot string.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Missouri State Plane Central Zone, NAD83, US Survey Feet -- the system
# como.gov's geox/geoy columns are published in. always_xy=True so
# .transform(x, y) takes/returns (easting, northing) / (lon, lat) order
# rather than the CRS's native axis order.
TRANSFORMER = Transformer.from_crs("ESRI:102697", "EPSG:4326", always_xy=True)

DB_DSN = {
    "host": os.environ.get("MANDI_GEO_DB_HOST", "localhost"),
    "dbname": os.environ.get("MANDI_GEO_DB_NAME", "mandi_geo"),
    "user": os.environ.get("MANDI_GEO_DB_USER", "mandi_geo"),
    "password": os.environ["MANDI_GEO_DB_PASSWORD"],
}

UPSERT_SQL = """
INSERT INTO fire_medical_calls (
    in_num, source_agency, call_datetime, address, apt_lot, nature,
    report_id, patrol_area, geox, geoy, geom, fetched_at
) VALUES (
    %(in_num)s, %(source_agency)s, %(call_datetime)s, %(address)s,
    %(apt_lot)s, %(nature)s, %(report_id)s, %(patrol_area)s,
    %(geox)s, %(geoy)s,
    CASE WHEN %(lon)s IS NULL THEN NULL
         ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
    END,
    %(fetched_at)s
)
ON CONFLICT (in_num) DO UPDATE SET
    source_agency = excluded.source_agency,
    call_datetime = excluded.call_datetime,
    address = excluded.address,
    apt_lot = excluded.apt_lot,
    nature = excluded.nature,
    report_id = excluded.report_id,
    patrol_area = excluded.patrol_area,
    geox = excluded.geox,
    geoy = excluded.geoy,
    geom = excluded.geom,
    fetched_at = excluded.fetched_at
"""


def fetch_csv():
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8-sig")


def clean(value):
    value = (value or "").strip()
    return value or None


def parse_row(raw):
    call_dt = datetime.strptime(raw["CallDateTime"], "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=TZ)

    geox_raw = clean(raw.get("geox"))
    geoy_raw = clean(raw.get("geoy"))
    geox = float(geox_raw) if geox_raw else None
    geoy = float(geoy_raw) if geoy_raw else None
    lon = lat = None
    if geox is not None and geoy is not None:
        lon, lat = TRANSFORMER.transform(geox, geoy)

    return {
        "in_num": int(raw["InNum"]),
        "source_agency": clean(raw.get("Agency")),
        "call_datetime": call_dt.isoformat(),
        "address": clean(raw.get("Address")),
        "apt_lot": clean(raw.get("AptLot")),
        "nature": clean(raw.get("ExtNatureDisplayName")),
        "report_id": clean(raw.get("Report")),
        "patrol_area": clean(raw.get("PolArea")),
        "geox": geox,
        "geoy": geoy,
        "lon": lon,
        "lat": lat,
    }


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    return [parse_row(row) for row in reader]


def upsert_calls(conn, rows, fetched_at):
    with conn.cursor() as cur:
        for row in rows:
            row["fetched_at"] = fetched_at
            cur.execute(UPSERT_SQL, row)


# Rolling window the dashboard shows -- matches the original spec's "last 24
# hours of calls" goal, not just "today" (a pure calendar-day window would
# empty out right after midnight even though the last few hours are still
# relevant).
RECENT_WINDOW = timedelta(hours=24)

RECENT_QUERY = """
SELECT in_num, call_datetime, address, nature, source_agency, patrol_area,
       ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat
FROM fire_medical_calls
WHERE call_datetime >= %(since)s
ORDER BY call_datetime DESC
"""


def query_recent(conn, now):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(RECENT_QUERY, {"since": now - RECENT_WINDOW})
        return cur.fetchall()


def maps_url(address):
    if not address:
        return None
    # Append city/state -- the source addresses are bare street-level text
    # (e.g. "S ANN ST/E BROADWAY"), which without a locality hint can geocode
    # to the wrong city entirely.
    query = quote_plus(f"{address}, Columbia, MO")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


# Dashboard color-coding categories (Grant's explicit mapping). Keyword
# substring match, not an exact lookup -- "Fire Alarm" needs to match
# "fire", not just the literal nature strings observed so far, so a new
# como.gov nature string that follows the same naming convention (e.g. some
# other "... Fire ..." label) still gets classified correctly. Checked in
# this order so a plain "Alarm" (ambiguous, no keyword match) stays
# uncolored rather than being guessed into a category.
NATURE_CATEGORY_KEYWORDS = [
    ("medical", "medical"),
    ("fire", "fire"),
    ("smoke", "smoke"),
    ("citizen", "citizen"),
    ("service", "citizen"),
]


def classify_nature(nature):
    if not nature:
        return None
    lowered = nature.lower()
    for keyword, category in NATURE_CATEGORY_KEYWORDS:
        if keyword in lowered:
            return category
    return None


# Dashboard icon for the Nature column, shown in front of the text on the
# fire/medical tab (not the color-coding categories above -- icons can be
# more granular since they don't need to collapse to just 4 buckets).
# Keyword substring match, same reasoning as NATURE_CATEGORY_KEYWORDS: a new
# como.gov nature string following an existing naming convention (e.g. some
# other "... Fire ..." label) still gets an icon. Checked in this order so
# more specific keywords (e.g. "carbon monoxide") are tried before more
# generic ones that would otherwise shadow them (e.g. bare "alarm").
NATURE_ICON_KEYWORDS = [
    ("carbon monoxide", "☠️"),   # toxic gas, distinct from a plain alarm
    ("medical", "🚑"),
    ("fire", "🔥"),              # covers "Fire Alarm" and "Vehicle Fire"
    ("smoke", "💨"),
    ("gas", "⛽"),                # "Gas Leak/Gas Odor"
    ("electrical", "⚡"),
    ("collision", "🚗"),         # "Motor Vehicle Collision" / "Vehicle Collision"
    ("citizen", "🤝"),
    ("service", "🤝"),           # "Citizen Assist/Service Call"
    ("knox", "🔑"),              # Knox Box building access
    ("unknown", "❓"),
    ("alarm", "🔔"),             # generic/uncategorized alarm
]


def nature_icon(nature):
    if not nature:
        return ""
    lowered = nature.lower()
    for keyword, icon in NATURE_ICON_KEYWORDS:
        if keyword in lowered:
            return icon
    return ""


def build_cache_payload(conn, now):
    calls = []
    for row in query_recent(conn, now):
        dt = row["call_datetime"]
        calls.append({
            "in_num": row["in_num"],
            "call_datetime": dt.isoformat(),
            "call_time_display": dt.astimezone(TZ).strftime("%b %-d, %-I:%M %p"),
            "address": row["address"],
            "address_maps_url": maps_url(row["address"]),
            "nature": row["nature"],
            "nature_category": classify_nature(row["nature"]),
            "nature_icon": nature_icon(row["nature"]),
            "source_agency": row["source_agency"],
            "patrol_area": row["patrol_area"],
            "lon": row["lon"],
            "lat": row["lat"],
        })
    return {
        "fetched_at": now.isoformat(),
        "fetched_at_display": now.strftime("%b %-d, %Y %-I:%M %p %Z"),
        "calls": calls,
    }


def write_cache(payload):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp_path = CACHE_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, CACHE_PATH)


def main():
    now = datetime.now(TZ)
    fetched_at = now.isoformat()
    try:
        text = fetch_csv()
    except Exception as exc:
        print(f"ERROR fetching fire CSV export: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = parse_csv(text)
    if not rows:
        print("WARN no rows parsed from fire CSV export", file=sys.stderr)
        return

    conn = psycopg2.connect(**DB_DSN)
    try:
        upsert_calls(conn, rows, fetched_at)
        conn.commit()
        write_cache(build_cache_payload(conn, now))
    finally:
        conn.close()

    print(f"upserted {len(rows)} fire/medical calls")


if __name__ == "__main__":
    main()
