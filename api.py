"""
Haze Monitor API — serves PSI/PM2.5 data from SQLite.
Run: .venv/bin/python api.py
Endpoints:
  GET /api/haze/latest        — latest readings for all regions
  GET /api/haze/history       — historical data (query: ?hours=24&region=central)
  GET /api/haze/summary       — text summary for Telegram
"""
import sqlite3
import json
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

DB_PATH = "/home/seongper/haze-monitor/haze.db"
REGIONS = ["north", "south", "east", "central", "west"]
REGION_LABELS = {"north": "North", "south": "South", "east": "East", "central": "Central", "west": "West"}

app = Flask(__name__)


def parse_time_arg(time_str):
    """Parse a time query param into a DB timestamp (rounded to the hour).

    Accepts:
      - "16:00", "16", "4pm", "4 pm", "4:30pm"  → today at that hour
      - "2026-09-30T16" / "2026-09-30 16:00"     → that date + hour
      - full ISO "2026-09-30T16:00:00+08:00"
    Returns an ISO string like "2026-09-30T16:00:00+08:00", or None if unparseable.
    """
    if not time_str:
        return None
    s = time_str.strip()
    now = datetime.now()

    # Full ISO with timezone / seconds
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2}):(\d{2})([+-]\d{2}:\d{2})?$", s)
    if m:
        y, mo, d, h, mi, sec, tz = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec))
        except ValueError:
            return None
        return dt.replace(minute=0, second=0).isoformat() + "+08:00"

    # Date + hour (no minutes/seconds): "2026-09-30T16" or "2026-09-30 16:00"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2})(?::(\d{2}))?$", s)
    if m:
        y, mo, d, h, mi = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi) if mi else 0)
        except ValueError:
            return None
        return dt.replace(minute=0, second=0).isoformat() + "+08:00"

    # "4pm" / "4 pm" / "4:30pm"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", s, re.IGNORECASE)
    if m:
        h, mi, ampm = m.groups()
        h = int(h)
        if ampm.lower() == "pm" and h != 12:
            h += 12
        if ampm.lower() == "am" and h == 12:
            h = 0
        return now.replace(hour=h, minute=0, second=0, microsecond=0).isoformat() + "+08:00"

    # "16:00" / "16"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?$", s)
    if m:
        h, mi = m.groups()
        h = int(h)
        if 0 <= h <= 23:
            return now.replace(hour=h, minute=0, second=0, microsecond=0).isoformat() + "+08:00"

    return None


def find_nearest_timestamp(conn, target_ts):
    """Return the exact timestamp if present, else the nearest earlier one."""
    row = conn.execute("SELECT MAX(timestamp) FROM readings WHERE timestamp <= ?", (target_ts,)).fetchone()
    if row and row[0]:
        return row[0]
    return None


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def psi_band(val):
    if val is None:
        return "unknown"
    if val <= 50:
        return "good"
    if val <= 100:
        return "moderate"
    if val <= 200:
        return "unhealthy"
    if val <= 300:
        return "very_unhealthy"
    return "hazardous"


def _rows_for_timestamp(conn, ts):
    return conn.execute(
        "SELECT region, psi_24h, pm25_24h, pm25_1h FROM readings WHERE timestamp = ?",
        (ts,)
    ).fetchall()


@app.route("/api/haze/latest")
def latest():
    time_arg = request.args.get("time") or request.args.get("at")
    conn = get_db()

    if time_arg:
        ts = parse_time_arg(time_arg)
        if ts is None:
            conn.close()
            return jsonify({"error": f"could not parse time: {time_arg}"}), 400
        ts = find_nearest_timestamp(conn, ts)
    else:
        row = conn.execute("SELECT MAX(timestamp) as ts FROM readings").fetchone()
        ts = row["ts"] if row else None

    if not ts:
        conn.close()
        return jsonify({"error": "no data"}), 404

    rows = _rows_for_timestamp(conn, ts)
    conn.close()

    regions = {}
    for r in rows:
        regions[r["region"]] = {
            "psi_24h": r["psi_24h"],
            "pm25_24h": r["pm25_24h"],
            "pm25_1h": r["pm25_1h"],
            "band": psi_band(r["psi_24h"]),
        }

    return jsonify({
        "timestamp": ts,
        "regions": regions,
    })


@app.route("/api/haze/history")
def history():
    hours = request.args.get("hours", 24, type=int)
    region = request.args.get("region", "central")
    if region not in REGIONS:
        return jsonify({"error": f"invalid region: {region}"}), 400

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    conn = get_db()
    rows = conn.execute(
        """SELECT timestamp, psi_24h, pm25_24h, pm25_1h
           FROM readings
           WHERE region = ? AND timestamp >= ?
           ORDER BY timestamp ASC""",
        (region, cutoff)
    ).fetchall()
    conn.close()

    return jsonify({
        "region": region,
        "points": [{
            "timestamp": r["timestamp"],
            "psi_24h": r["psi_24h"],
            "pm25_24h": r["pm25_24h"],
            "pm25_1h": r["pm25_1h"],
        } for r in rows]
    })


@app.route("/api/haze/summary")
def summary():
    time_arg = request.args.get("time") or request.args.get("at")
    conn = get_db()

    if time_arg:
        ts = parse_time_arg(time_arg)
        if ts is None:
            conn.close()
            return f"Could not parse time: {time_arg}", 400
        ts = find_nearest_timestamp(conn, ts)
    else:
        row = conn.execute("SELECT MAX(timestamp) as ts FROM readings").fetchone()
        ts = row["ts"] if row else None

    if not ts:
        conn.close()
        return "No haze data available yet.", 404

    rows = conn.execute(
        "SELECT region, psi_24h, pm25_1h FROM readings WHERE timestamp = ? ORDER BY psi_24h DESC",
        (ts,)
    ).fetchall()
    conn.close()

    lines = [f"🇸🇬 Singapore Haze — {ts}", ""]
    worst = rows[0] if rows else None
    if worst and worst["psi_24h"]:
        band_str = psi_band(worst["psi_24h"]).replace("_", " ").title()
        lines.append(f"Worst: {REGION_LABELS[worst['region']]} at {worst['psi_24h']} PSI ({band_str})")
    lines.append("")

    for r in rows:
        p = r["psi_24h"] or "?"
        m = r["pm25_1h"] or "?"
        emoji = {"good": "🟢", "moderate": "🔵", "unhealthy": "🟠", "very_unhealthy": "🔴", "hazardous": "🟣"}.get(psi_band(r["psi_24h"]), "⚪")
        lines.append(f"{emoji} {REGION_LABELS[r['region']]:8s} PSI {str(p):>4s}  PM2.5 {str(m):>4s} µg/m³")

    return "\n".join(lines)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8095, debug=False)