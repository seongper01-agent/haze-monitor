"""
Haze Monitor API — Vercel serverless + Supabase.
Reads from Supabase, falls back to live data.gov.sg fetch.
"""
import os
import requests as req
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"
REGIONS = ["north", "south", "east", "central", "west"]
LABELS = {"north": "North", "south": "South", "east": "East", "central": "Central", "west": "West"}


def sb_headers():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def psi_band(val):
    if val is None: return "unknown"
    if val <= 50: return "good"
    if val <= 100: return "moderate"
    if val <= 200: return "unhealthy"
    if val <= 300: return "very_unhealthy"
    return "hazardous"


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def _get_latest_data():
    """Returns (data_dict, error_msg). One of them will be set."""
    # Try Supabase first
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
                data = r2.json()
                regions = {}
                for row in data:
                    regions[row["region"]] = {
                        "psi_24h": row.get("psi_24h"), "pm25_24h": row.get("pm25_24h"),
                        "pm25_1h": row.get("pm25_1h"), "band": psi_band(row.get("psi_24h")),
                    }
                if regions:
                    return ({"timestamp": ts, "regions": regions}, None)
        except Exception:
            pass

    # Fallback: live from data.gov.sg
    try:
        psi = req.get(PSI_URL, timeout=10).json()["items"][0]
        pm25 = req.get(PM25_URL, timeout=10).json()["items"][0]
        psi_24h = psi["readings"]["psi_twenty_four_hourly"]
        pm25_1h = pm25["readings"]["pm25_one_hourly"]
        pm25_24h = psi["readings"].get("pm25_twenty_four_hourly", {})
    except Exception as e:
        return (None, str(e))

    regions = {}
    for r in REGIONS:
        regions[r] = {
            "psi_24h": psi_24h.get(r), "pm25_24h": pm25_24h.get(r),
            "pm25_1h": pm25_1h.get(r), "band": psi_band(psi_24h.get(r)),
        }
    return ({"timestamp": psi["timestamp"], "regions": regions}, None)


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
    if not SUPABASE_URL:
        return jsonify({"region": region, "points": [], "note": "Supabase not configured"})

    cutoff = (datetime.utcnow() - timedelta(hours=h+8)).isoformat() + "+08:00"
    try:
        r = req.get(
            f"{SUPABASE_URL}/rest/v1/readings"
            f"?region=eq.{region}&timestamp=gte.{cutoff}&order=timestamp.asc",
            headers=sb_headers(), timeout=10
        )
        return jsonify({"region": region, "points": [{
            "timestamp": row["timestamp"], "psi_24h": row.get("psi_24h"),
            "pm25_24h": row.get("pm25_24h"), "pm25_1h": row.get("pm25_1h"),
        } for row in r.json()]})
    except Exception as e:
        return jsonify({"region": region, "points": [], "error": str(e)})


@app.route("/")
@app.route("/<path:path>")
def serve_frontend(path="index.html"):
    import os as _os
    static_dir = _os.path.join(_os.path.dirname(__file__), "static")
    full = _os.path.join(static_dir, path)
    if _os.path.exists(full):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, "index.html")