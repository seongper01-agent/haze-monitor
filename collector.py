"""
PSI/PM2.5 data collector — fetches from data.gov.sg and stores in SQLite and/or Supabase.
Run: .venv/bin/python collector.py
- SQLite: stores to /home/seongper/haze-monitor/haze.db if the DB is accessible
- Supabase: stores if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in .env
"""
import os
import sqlite3
import requests
import sys
from datetime import datetime

DB_PATH = "/home/seongper/haze-monitor/haze.db"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]


def load_env(path):
    """Load KEY=VALUE pairs from a .env file into os.environ (does not overwrite)."""
    if not os.path.isfile(path):
        return False
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val
    return True


def sqlite_available():
    """Return a writable connection if the SQLite DB is accessible, else None."""
    try:
        conn = sqlite3.connect(DB_PATH)
        # Quick write test: create table to verify we can write
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
        print(f"SQLite unavailable ({DB_PATH}): {e}", file=sys.stderr)
        return None


def supabase_available():
    """Return True if Supabase env vars are present after loading .env."""
    load_env(ENV_PATH)
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if url and key:
        return {"url": url, "key": key}
    return None


def supabase_headers(sb):
    return {
        "apikey": sb["key"],
        "Authorization": f"Bearer {sb['key']}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def store_sqlite(conn, ts, psi_24h, pm25_24h, pm25_1h):
    """Store readings in SQLite. Returns count stored."""
    stored = 0
    for region in REGIONS:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO readings (timestamp, region, psi_24h, pm25_24h, pm25_1h)
                VALUES (?, ?, ?, ?, ?)
            """, (ts, region, psi_24h.get(region), pm25_24h.get(region), pm25_1h.get(region)))
            stored += 1
        except Exception as e:
            print(f"ERROR sqlite {region}: {e}", file=sys.stderr)
    conn.commit()
    return stored


def store_supabase(sb, ts, psi_24h, pm25_24h, pm25_1h):
    """Store readings in Supabase. Returns count stored."""
    stored = 0
    for region in REGIONS:
        payload = {
            "timestamp": ts,
            "region": region,
            "psi_24h": psi_24h.get(region),
            "pm25_24h": pm25_24h.get(region),
            "pm25_1h": pm25_1h.get(region),
        }
        try:
            r = requests.post(
                f"{sb['url']}/rest/v1/readings",
                json=payload,
                headers=supabase_headers(sb),
                timeout=10,
            )
            if r.status_code in (200, 201, 204):
                stored += 1
            else:
                print(f"WARN supabase {region}: {r.status_code} {r.text}", file=sys.stderr)
        except Exception as e:
            print(f"ERROR supabase {region}: {e}", file=sys.stderr)
    return stored


def fetch_and_store():
    # --- Fetch data from data.gov.sg ---
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

    # --- Check availability ---
    conn = sqlite_available()
    sb = supabase_available()

    if not conn and not sb:
        print("ERROR: neither SQLite nor Supabase is available — nothing stored", file=sys.stderr)
        sys.exit(1)

    # --- Store ---
    sqlite_count = 0
    sb_count = 0

    if conn:
        sqlite_count = store_sqlite(conn, ts, psi_24h, pm25_24h, pm25_1h)
        conn.close()
        print(f"SQLite: stored {sqlite_count}/{len(REGIONS)} readings")

    if sb:
        sb_count = store_supabase(sb, ts, psi_24h, pm25_24h, pm25_1h)
        print(f"Supabase: stored {sb_count}/{len(REGIONS)} readings")

    print(f"[{datetime.now().isoformat()}] Done — SQLite={sqlite_count}, Supabase={sb_count}")
    return sqlite_count + sb_count


if __name__ == "__main__":
    fetch_and_store()