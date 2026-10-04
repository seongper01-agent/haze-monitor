"""
Haze Monitor API — Vercel-ready Flask app.
Data sources (tried in order): local SQLite → Supabase → data.gov.sg live.
"""
import os
import sqlite3
import requests as req
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# ── Config from env ──
HAZE_DB_PATH = os.environ.get("HAZE_DB_PATH", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]
LABELS = {"north": "North", "south": "South", "east": "East", "central": "Central", "west": "West"}


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
    regions = {}
    for row in rows:
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
    """Try SQLite → Supabase → data.gov.sg. Returns (data_dict, error_msg)."""

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
        try:
            r = req.get(
                f"{SUPABASE_URL}/rest/v1/readings?select=timestamp&order=timestamp.desc&limit=1",
                headers=sb_headers(), timeout=8
            )
            rows = r.json()
            if rows:
                ts = rows[0]["timestamp"]
                r2 = req.get(
                    f"{SUPABASE_URL}/rest/v1/readings?timestamp=eq.{ts}",
                    headers=sb_headers(), timeout=8
                )
                regions = _rows_to_regions(r2.json())
                if regions:
                    app.logger.info("haze: source=supabase ts=%s regions=%d", ts, len(regions))
                    return ({"timestamp": ts, "regions": regions}, None)
        except Exception as e:
            app.logger.warning("haze: supabase failed: %s", e)

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
    h = request.args.get("hours", 24, type=int)
    region = request.args.get("region", "central")

    # 1. Local SQLite
    cutoff = (datetime.utcnow() - timedelta(hours=h+8)).isoformat() + "+08:00"
    rows = _sqlite_query(
        "SELECT timestamp, psi_24h, pm25_24h, pm25_1h FROM readings WHERE region = ? AND timestamp >= ? ORDER BY timestamp ASC",
        (region, cutoff)
    )
    if rows is not None:
        return jsonify({"region": region, "points": rows})

    # 2. Supabase
    if SUPABASE_URL:
        try:
            r = req.get(
                f"{SUPABASE_URL}/rest/v1/readings?region=eq.{region}&timestamp=gte.{cutoff}&order=timestamp.asc",
                headers=sb_headers(), timeout=10
            )
            return jsonify({"region": region, "points": r.json()})
        except Exception as e:
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