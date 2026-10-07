"""
Haze Monitor API - Vercel-ready Flask app.
Data sources (tried in order): local SQLite -> Supabase -> data.gov.sg live.
"""
import os
import sys
import logging
import sqlite3
import requests as req
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# ── Logging to stderr ──
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.DEBUG)
_stderr_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
))
# Remove Flask's default handler to avoid duplicate output
app.logger.handlers.clear()
app.logger.addHandler(_stderr_handler)
app.logger.setLevel(logging.DEBUG)

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

# ── Config from env ──
HAZE_DB_PATH = os.path.expandvars(os.path.expanduser(
    os.environ.get("HAZE_DB_PATH", "")))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]
LABELS = {"north": "North", "south": "South", "east": "East", "central": "Central", "west": "West"}

app.logger.info("haze: startup — Supabase %s, SQLite %s",
                "configured" if (SUPABASE_URL and SUPABASE_KEY) else "not configured",
                f"at {HAZE_DB_PATH}" if (HAZE_DB_PATH and os.path.exists(HAZE_DB_PATH)) else "unavailable")


# ── Helpers ──

def sb_headers():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def psi_band(val):
    if val is None: return "unknown"
    if val <= 50: return "good"
    if val <= 100: return "moderate"
    if val <= 200: return "unhealthy"
    if val <= 300: return "very_unhealthy"
    return "hazardous"


def _sqlite_query(query, params=(), fetch_one=False):
    """Run a query on the local SQLite DB. Returns rows as dicts, or None if DB unavailable."""
    if not HAZE_DB_PATH or not os.path.exists(HAZE_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(HAZE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows[0] if fetch_one else rows
    except Exception:
        return None


def _rows_to_regions(rows):
    """Convert Supabase/SQLite rows to {region: {psi_24h, pm25_24h, pm25_1h, band}}."""
    if not isinstance(rows, list):
        app.logger.warning("haze: _rows_to_regions expected list, got %s", type(rows).__name__)
        return {}
    regions = {}
    for row in rows:
        if not isinstance(row, dict):
            app.logger.warning("haze: _rows_to_regions skipping non-dict row: %s", type(row).__name__)
            continue
        regions[row["region"]] = {
            "psi_24h": row.get("psi_24h"), "pm25_24h": row.get("pm25_24h"),
            "pm25_1h": row.get("pm25_1h"), "band": psi_band(row.get("psi_24h")),
        }
    return regions


def _fetch_live():
    """Fetch latest from data.gov.sg. Returns (data_dict, error_msg)."""
    try:
        psi = req.get(PSI_URL, timeout=10).json()["items"][0]
        pm25 = req.get(PM25_URL, timeout=10).json()["items"][0]
    except Exception as e:
        return None, str(e)
    psi_24h = psi["readings"]["psi_twenty_four_hourly"]
    pm25_1h = pm25["readings"]["pm25_one_hourly"]
    pm25_24h = psi["readings"].get("pm25_twenty_four_hourly", {})
    regions = {}
    for r in REGIONS:
        regions[r] = {
            "psi_24h": psi_24h.get(r), "pm25_24h": pm25_24h.get(r),
            "pm25_1h": pm25_1h.get(r), "band": psi_band(psi_24h.get(r)),
        }
    return {"timestamp": psi["timestamp"], "regions": regions}, None


# ── Data source cascade ──

def _get_latest_data():
    """Try SQLite -> Supabase -> data.gov.sg. Returns (data_dict, error_msg)."""

    # 1. Local SQLite
    row = _sqlite_query("SELECT MAX(timestamp) as ts FROM readings", fetch_one=True)
    if row and row["ts"]:
        rows = _sqlite_query("SELECT region, psi_24h, pm25_24h, pm25_1h FROM readings WHERE timestamp = ?", (row["ts"],))
        if rows:
            app.logger.info("haze: source=sqlite ts=%s regions=%d", row["ts"], len(rows))
            return ({"timestamp": row["ts"], "regions": _rows_to_regions(rows)}, None)
    else:
        app.logger.debug("haze: sqlite unavailable (HAZE_DB_PATH=%s)", HAZE_DB_PATH or "not set")

    # 2. Supabase
    if SUPABASE_URL and SUPABASE_KEY:
        app.logger.info("haze: attempting Supabase connection to %s", SUPABASE_URL)
        try:
            r = req.get(
                f"{SUPABASE_URL}/rest/v1/readings",
                params={"select": "timestamp", "order": "timestamp.desc", "limit": "1"},
                headers=sb_headers(), timeout=8
            )
            app.logger.info("haze: Supabase connection OK (HTTP %d)", r.status_code)
            rows = r.json()
            if isinstance(rows, list) and rows:
                ts = rows[0].get("timestamp") if isinstance(rows[0], dict) else None
                if ts:
                    r2 = req.get(
                        f"{SUPABASE_URL}/rest/v1/readings",
                        params={"timestamp": f"eq.{ts}"},
                        headers=sb_headers(), timeout=8
                    )
                    app.logger.info("haze: Supabase data retrieval OK (HTTP %d), %d rows", r2.status_code, len(r2.json()) if isinstance(r2.json(), list) else 0)
                    regions = _rows_to_regions(r2.json())
                    if regions:
                        app.logger.info("haze: source=supabase ts=%s regions=%d", ts, len(regions))
                        return ({"timestamp": ts, "regions": regions}, None)
                    else:
                        app.logger.warning("haze: Supabase data retrieval returned empty regions")
                else:
                    app.logger.warning("haze: Supabase returned no timestamp in latest row")
            else:
                app.logger.warning("haze: Supabase returned unexpected shape: %s",
                                   type(rows).__name__ if not isinstance(rows, list) else f"list({len(rows)})")
        except Exception as e:
            app.logger.error("haze: Supabase connection/data retrieval FAILED: %s", e)
    else:
        app.logger.debug("haze: Supabase skipped (SUPABASE_URL=%s, SUPABASE_KEY=%s)",
                         "set" if SUPABASE_URL else "not set",
                         "set" if SUPABASE_KEY else "not set")

    # 3. data.gov.sg live
    app.logger.info("haze: source=datagovsg (fallback)")
    data, err = _fetch_live()
    if data:
        app.logger.info("haze: source=datagovsg ts=%s", data["timestamp"])
    return data, err


# ── Routes ──

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/haze/latest")
def latest():
    data, err = _get_latest_data()
    if err:
        return jsonify({"error": err}), 502
    return jsonify(data)


@app.route("/api/haze/summary")
def summary():
    data, err = _get_latest_data()
    if err:
        return f"Error: {err}", 502
    ts = data["timestamp"]
    regs = data["regions"]
    sorted_r = sorted(REGIONS, key=lambda r: regs.get(r, {}).get("psi_24h", 0), reverse=True)
    lines = [f"🇸🇬 Singapore Haze — {ts}", ""]
    worst = sorted_r[0]
    wv = regs[worst]["psi_24h"]
    lines.append(f"Worst: {LABELS[worst]} at {wv} PSI ({psi_band(wv).replace('_',' ').title()})")
    lines.append("")
    emoji = {"good": "🟢", "moderate": "🔵", "unhealthy": "🟠", "very_unhealthy": "🔴", "hazardous": "🟣"}
    for r in sorted_r:
        d = regs.get(r, {})
        lines.append(f"{emoji.get(psi_band(d.get('psi_24h')),'⚪')} {LABELS[r]:8s} PSI {str(d.get('psi_24h','?')):>4s}  PM2.5 {str(d.get('pm25_1h','?')):>4s} µg/m³")
    return "\n".join(lines)


@app.route("/api/haze/history")
def history():
    region = request.args.get("region", "central")
    start = request.args.get("start")
    end = request.args.get("end")

    if start and end:
        cutoff_start = start
        cutoff_end = end
    else:
        h = request.args.get("hours", 24, type=int)
        cutoff_end = (datetime.utcnow()).isoformat() + "+08:00"
        cutoff_start = (datetime.utcnow() - timedelta(hours=h+8)).isoformat() + "+08:00"

    # 1. Local SQLite
    rows = _sqlite_query(
        "SELECT timestamp, psi_24h, pm25_24h, pm25_1h FROM readings WHERE region = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
        (region, cutoff_start, cutoff_end)
    )
    if rows is not None:
        app.logger.info("haze history: source=sqlite region=%s start=%s end=%s points=%d", region, cutoff_start, cutoff_end, len(rows))
        return jsonify({"region": region, "points": rows})

    # 2. Supabase
    if SUPABASE_URL:
        app.logger.info("haze history: attempting Supabase connection to %s", SUPABASE_URL)
        try:
            r = req.get(
                f"{SUPABASE_URL}/rest/v1/readings",
                params={
                    "region": f"eq.{region}",
                    "timestamp": f"gte.{cutoff_start}",
                    "and": f"(timestamp.lte.{cutoff_end})",
                    "order": "timestamp.asc",
                },
                headers=sb_headers(), timeout=10
            )
            data = r.json()
            app.logger.info("haze history: Supabase data retrieval OK (HTTP %d), %d points",
                           r.status_code, len(data) if isinstance(data, list) else 0)
            return jsonify({"region": region, "points": data})
        except Exception as e:
            app.logger.error("haze history: Supabase connection/data retrieval FAILED: %s", e)
            return jsonify({"region": region, "points": [], "error": str(e)})

    return jsonify({"region": region, "points": [], "note": "No data source configured"})


@app.route("/")
@app.route("/<path:path>")
def serve_frontend(path="index.html"):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    full = os.path.join(static_dir, path)
    if os.path.exists(full):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, "index.html")
