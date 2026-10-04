"""
Haze collector — fetches PSI/PM2.5 from data.gov.sg, stores in SQLite + Supabase.
Writes to SQLite if HAZE_DB_PATH is set, and to Supabase if creds are set.
"""
import os
import sys
import sqlite3
import requests
from datetime import datetime

# ── Config from env ──
HAZE_DB_PATH = os.environ.get("HAZE_DB_PATH", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]


def init_sqlite():
    """Ensure the local SQLite DB and table exist."""
    if not HAZE_DB_PATH:
        return None
    try:
        conn = sqlite3.connect(HAZE_DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                region TEXT NOT NULL,
                psi_24h INTEGER,
                pm25_24h INTEGER,
                pm25_1h INTEGER,
                UNIQUE(timestamp, region)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_region ON readings(region)")
        conn.commit()
        return conn
    except Exception as e:
        print(f"WARN: SQLite init failed: {e}", file=sys.stderr)
        return None


def store_sqlite(conn, ts, psi_24h, pm25_24h, pm25_1h):
    """Store readings in local SQLite. Returns count stored."""
    if conn is None:
        return 0
    stored = 0
    for region in REGIONS:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO readings (timestamp, region, psi_24h, pm25_24h, pm25_1h)
                VALUES (?, ?, ?, ?, ?)
            """, (ts, region, psi_24h.get(region), pm25_24h.get(region), pm25_1h.get(region)))
            stored += 1
        except Exception as e:
            print(f"WARN: SQLite store {region}: {e}", file=sys.stderr)
    conn.commit()
    return stored


def store_supabase(ts, psi_24h, pm25_24h, pm25_1h):
    """Store readings in Supabase. Returns count stored."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    headers = {
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates",
    }
    stored = 0
    for region in REGIONS:
        payload = {
            "timestamp": ts, "region": region,
            "psi_24h": psi_24h.get(region),
            "pm25_24h": pm25_24h.get(region),
            "pm25_1h": pm25_1h.get(region),
        }
        try:
            r = requests.post(f"{SUPABASE_URL}/rest/v1/readings", json=payload, headers=headers, timeout=10)
            if r.status_code in (200, 201, 204):
                stored += 1
        except Exception:
            pass
    return stored


def fetch_and_store():
    sqlite = init_sqlite()

    try:
        psi_resp = requests.get(PSI_URL, timeout=15).json()
        pm25_resp = requests.get(PM25_URL, timeout=15).json()
    except Exception as e:
        print(f"ERROR fetching data: {e}", file=sys.stderr)
        sys.exit(1)

    psi_item = psi_resp["items"][0]
    pm25_item = pm25_resp["items"][0]
    ts = psi_item["timestamp"]
    psi_24h = psi_item["readings"]["psi_twenty_four_hourly"]
    pm25_24h = psi_item["readings"].get("pm25_twenty_four_hourly", {})
    pm25_1h = pm25_item["readings"]["pm25_one_hourly"]

    n_sqlite = store_sqlite(sqlite, ts, psi_24h, pm25_24h, pm25_1h)
    n_supabase = store_supabase(ts, psi_24h, pm25_24h, pm25_1h)

    parts = []
    if n_sqlite: parts.append(f"SQLite={n_sqlite}")
    if n_supabase: parts.append(f"Supabase={n_supabase}")
    print(f"[{datetime.now().isoformat()}] Stored {', '.join(parts)} for {ts}")

    if sqlite:
        sqlite.close()


if __name__ == "__main__":
    fetch_and_store()