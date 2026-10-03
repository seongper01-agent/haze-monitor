"""
Haze collector — fetches PSI/PM2.5 from data.gov.sg and stores in Supabase.
Run: python collector.py
Env vars needed: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
import os
import sys
import requests
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]


def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def fetch_and_store():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        sys.exit(1)

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
        payload = {
            "timestamp": ts,
            "region": region,
            "psi_24h": psi_24h.get(region),
            "pm25_24h": pm25_24h.get(region),
            "pm25_1h": pm25_1h.get(region),
        }
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/readings",
                json=payload,
                headers=sb_headers(),
                timeout=10,
            )
            if r.status_code in (200, 201, 204):
                stored += 1
            else:
                print(f"WARN: store {region}: {r.status_code} {r.text}", file=sys.stderr)
        except Exception as e:
            print(f"ERROR storing {region}: {e}", file=sys.stderr)

    print(f"[{datetime.now().isoformat()}] Stored {stored}/{len(REGIONS)} readings for {ts}")


if __name__ == "__main__":
    fetch_and_store()