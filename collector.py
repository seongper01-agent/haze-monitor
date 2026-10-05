"""
Haze collector - fetches PSI/PM2.5 from data.gov.sg, stores in SQLite + Supabase.
Writes to SQLite if HAZE_DB_PATH is set, and to Supabase if creds are set.
"""
import os
import sys
import logging
import sqlite3
import requests
from datetime import datetime

# ── .env loader (zero-dependency) ──
def _load_dotenv(env_path=None):
    """Load key=value pairs from a .env file into os.environ (does not overwrite)."""
    if env_path is None:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val

_load_dotenv()

# ── Logging ──
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "collector.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-7s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("haze-collector")

# ── Config from env ──
HAZE_DB_PATH = os.path.expandvars(os.path.expanduser(
    os.environ.get("HAZE_DB_PATH", "")))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]


def init_sqlite():
    """Ensure the local SQLite DB and table exist."""
    if not HAZE_DB_PATH:
        log.debug("SQLite: HAZE_DB_PATH not set — skipping")
        return None
    log.debug("SQLite: attempting connection to %s", HAZE_DB_PATH)
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
        log.debug("SQLite: connected and table ready at %s", HAZE_DB_PATH)
        return conn
    except Exception as e:
        log.error("SQLite: init failed for %s — %s", HAZE_DB_PATH, e)
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
            log.debug("SQLite: wrote %s (psi=%s, pm25_24h=%s, pm25_1h=%s)",
                      region, psi_24h.get(region), pm25_24h.get(region), pm25_1h.get(region))
        except Exception as e:
            log.error("SQLite: failed to write %s — %s", region, e)
    conn.commit()
    if stored > 0:
        log.info("SQLite: successfully stored %d/%d regions", stored, len(REGIONS))
    else:
        log.warning("SQLite: 0 regions stored")
    return stored


def store_supabase(ts, psi_24h, pm25_24h, pm25_1h):
    """Store readings in Supabase. Returns count stored."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.debug("Supabase: SUPABASE_URL or SUPABASE_KEY not set — skipping")
        return 0
    log.debug("Supabase: attempting writes to %s", SUPABASE_URL)
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
                log.debug("Supabase: wrote %s (HTTP %d)", region, r.status_code)
            else:
                log.error("Supabase: failed to write %s — HTTP %d: %s", region, r.status_code, r.text[:200])
        except Exception as e:
            log.error("Supabase: failed to write %s — %s", region, e)
    if stored > 0:
        log.info("Supabase: successfully stored %d/%d regions", stored, len(REGIONS))
    else:
        log.warning("Supabase: 0 regions stored")
    return stored


def fetch_and_store():
    # ── Show what databases we're targeting ──
    targets = []
    if HAZE_DB_PATH:
        targets.append(f"SQLite ({HAZE_DB_PATH})")
    else:
        targets.append("SQLite (disabled)")
    if SUPABASE_URL:
        targets.append(f"Supabase ({SUPABASE_URL})")
    else:
        targets.append("Supabase (disabled)")
    log.debug("Databases: %s", ", ".join(targets))

    sqlite = init_sqlite()

    try:
        psi_resp = requests.get(PSI_URL, timeout=15).json()
        pm25_resp = requests.get(PM25_URL, timeout=15).json()
    except Exception as e:
        log.error("Failed to fetch data from data.gov.sg: %s", e)
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
    log.info("Run complete — %s for %s", ", ".join(parts) if parts else "no writes", ts)
    print(f"[{datetime.now().isoformat()}] Stored {', '.join(parts)} for {ts}")

    if sqlite:
        sqlite.close()


if __name__ == "__main__":
    fetch_and_store()