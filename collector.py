"""
PSI/PM2.5 data collector — fetches from data.gov.sg and stores in SQLite.
Run: .venv/bin/python collector.py
"""
import sqlite3
import requests
import sys
from datetime import datetime

DB_PATH = "/home/seongper/haze-monitor/haze.db"
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]


def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_readings_region ON readings(region)
    """)
    conn.commit()
    return conn


def fetch_and_store():
    conn = init_db()

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

    stored = 0
    for region in REGIONS:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO readings (timestamp, region, psi_24h, pm25_24h, pm25_1h)
                VALUES (?, ?, ?, ?, ?)
            """, (
                ts,
                region,
                psi_24h.get(region),
                pm25_24h.get(region),
                pm25_1h.get(region)
            ))
            stored += 1
        except Exception as e:
            print(f"ERROR storing {region}: {e}", file=sys.stderr)

    conn.commit()
    conn.close()
    print(f"[{datetime.now().isoformat()}] Stored {stored} readings for {ts}")
    return stored


if __name__ == "__main__":
    fetch_and_store()