#!/usr/bin/env python3
"""Fetch Columbia Police Dispatch calls from como.gov's CSV export, upsert
every row into the mandi_geo PostgreSQL database (table `police_calls`),
then query the last rolling 24h back out of the DB and atomically rebuild
the JSON cache Home Assistant reads
($HA_WWW_DIR/columbia_911/police.json).

Design/rationale: docs/tender-wibbling-treehouse.md
Source research: police_csvexport.php has no geox/geoy columns at all
(unlike fire_csvexport.php) -- Columbia's police feed is officially ~6
hours delayed per city policy, so there's no near-real-time expectation
here; polled every 30 minutes rather than fire/medical's 5.

Run standalone to test: python3 columbia-911-police-fetch.py
Runs on a schedule via the columbia-911-police-fetch.timer systemd unit.
"""
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

URL = "https://www.como.gov/CMS/911dispatch/police_csvexport.php"
TZ = ZoneInfo("America/Chicago")
CACHE_PATH = os.path.join(
    os.path.expanduser(os.environ.get("HA_WWW_DIR", "~/homeassistant/config/www")),
    "columbia_911", "police.json",
)

# como.gov's WAF blocks automation-signature User-Agents (see
# columbia-911-fire-fetch.py) -- use a real browser UA, not a
# self-identifying bot string.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

DB_DSN = {
    "host": os.environ.get("MANDI_GEO_DB_HOST", "localhost"),
    "dbname": os.environ.get("MANDI_GEO_DB_NAME", "mandi_geo"),
    "user": os.environ.get("MANDI_GEO_DB_USER", "mandi_geo"),
    "password": os.environ["MANDI_GEO_DB_PASSWORD"],
}

UPSERT_SQL = """
INSERT INTO police_calls (
    in_num, call_datetime, address, nature, report_id, patrol_area, fetched_at
) VALUES (
    %(in_num)s, %(call_datetime)s, %(address)s, %(nature)s,
    %(report_id)s, %(patrol_area)s, %(fetched_at)s
)
ON CONFLICT (in_num) DO UPDATE SET
    call_datetime = excluded.call_datetime,
    address = excluded.address,
    nature = excluded.nature,
    report_id = excluded.report_id,
    patrol_area = excluded.patrol_area,
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
    return {
        "in_num": int(raw["InNum"]),
        "call_datetime": call_dt.isoformat(),
        "address": clean(raw.get("Address")),
        "nature": clean(raw.get("ExtNatureDisplayName")),
        "report_id": clean(raw.get("Report")),
        "patrol_area": clean(raw.get("PolArea")),
    }


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    return [parse_row(row) for row in reader]


def upsert_calls(conn, rows, fetched_at):
    with conn.cursor() as cur:
        for row in rows:
            row["fetched_at"] = fetched_at
            cur.execute(UPSERT_SQL, row)


# Same rolling-window rationale as columbia-911-fire-fetch.py: "last 24
# hours", not a pure calendar-day window.
RECENT_WINDOW = timedelta(hours=24)

RECENT_QUERY = """
SELECT in_num, call_datetime, address, nature, patrol_area
FROM police_calls
WHERE call_datetime >= %(since)s
ORDER BY call_datetime DESC
"""


def query_recent(conn, now):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(RECENT_QUERY, {"since": now - RECENT_WINDOW})
        return cur.fetchall()


# Police addresses are block-level, not exact ("1400 BLOCK  RANGE LINE ST"),
# unlike fire's exact house-number addresses -- "BLOCK" isn't part of the
# street name and confuses a map geocoder if left in, so it's stripped from
# the query (the displayed address text is left as-is).
BLOCK_WORD_RE = re.compile(r"\bBLOCK\b", re.IGNORECASE)


def maps_url(address):
    if not address:
        return None
    without_block = BLOCK_WORD_RE.sub("", address)
    normalized = " ".join(without_block.split())
    query = quote_plus(f"{normalized}, Columbia, MO")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


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
            "patrol_area": row["patrol_area"],
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
        print(f"ERROR fetching police CSV export: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = parse_csv(text)
    if not rows:
        print("WARN no rows parsed from police CSV export", file=sys.stderr)
        return

    conn = psycopg2.connect(**DB_DSN)
    try:
        upsert_calls(conn, rows, fetched_at)
        conn.commit()
        write_cache(build_cache_payload(conn, now))
    finally:
        conn.close()

    print(f"upserted {len(rows)} police calls")


if __name__ == "__main__":
    main()
