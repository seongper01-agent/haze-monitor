"""
Haze Monitor API — Vercel serverless version.
Fetches live from data.gov.sg (no SQLite needed).
"""
import re
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

REGIONS = ["north", "south", "east", "central", "west"]
REGION_LABELS = {"north": "North", "south": "South", "east": "East", "central": "Central", "west": "West"}

# Vercel-compatible: fetch live on every request
import requests as req
PSI_URL = "https://api.data.gov.sg/v1/environment/psi"
PM25_URL = "https://api.data.gov.sg/v1/environment/pm25"


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def psi_band(val):
    if val is None:
        return "unknown"
    if val <= 50: return "good"
    if val <= 100: return "moderate"
    if val <= 200: return "unhealthy"
    if val <= 300: return "very_unhealthy"
    return "hazardous"


@app.route("/api/haze/latest")
def latest():
    try:
        psi_resp = req.get(PSI_URL, timeout=10).json()
        pm25_resp = req.get(PM25_URL, timeout=10).json()
        psi_item = psi_resp["items"][0]
        pm25_item = pm25_resp["items"][0]
        ts = psi_item["timestamp"]
        psi_24h = psi_item["readings"]["psi_twenty_four_hourly"]
        pm25_1h = pm25_item["readings"]["pm25_one_hourly"]
        pm25_24h = psi_item["readings"].get("pm25_twenty_four_hourly", {})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    regions = {}
    for r in REGIONS:
        regions[r] = {
            "psi_24h": psi_24h.get(r),
            "pm25_24h": pm25_24h.get(r),
            "pm25_1h": pm25_1h.get(r),
            "band": psi_band(psi_24h.get(r)),
        }

    return jsonify({"timestamp": ts, "regions": regions})


@app.route("/api/haze/summary")
def summary():
    try:
        psi_resp = req.get(PSI_URL, timeout=10).json()
        pm25_resp = req.get(PM25_URL, timeout=10).json()
        psi_item = psi_resp["items"][0]
        pm25_item = pm25_resp["items"][0]
        ts = psi_item["timestamp"]
        psi_24h = psi_item["readings"]["psi_twenty_four_hourly"]
        pm25_1h = pm25_item["readings"]["pm25_one_hourly"]
    except Exception as e:
        return f"Error: {e}", 502

    sorted_regions = sorted(REGIONS, key=lambda r: psi_24h.get(r, 0), reverse=True)
    lines = [f"🇸🇬 Singapore Haze — {ts}", ""]
    worst_region = sorted_regions[0]
    worst_val = psi_24h.get(worst_region, 0)
    band_str = psi_band(worst_val).replace("_", " ").title()
    lines.append(f"Worst: {REGION_LABELS[worst_region]} at {worst_val} PSI ({band_str})")
    lines.append("")

    for r in sorted_regions:
        p = psi_24h.get(r, "?")
        m = pm25_1h.get(r, "?")
        emoji = {"good": "🟢", "moderate": "🔵", "unhealthy": "🟠", "very_unhealthy": "🔴", "hazardous": "🟣"}.get(psi_band(p), "⚪")
        lines.append(f"{emoji} {REGION_LABELS[r]:8s} PSI {str(p):>4s}  PM2.5 {str(m):>4s} µg/m³")

    return "\n".join(lines)


@app.route("/api/haze/history")
def history():
    """Note: Vercel is stateless — returns empty. Full history needs a database."""
    return jsonify({"region": "central", "points": [], "note": "History not available on Vercel. Deploy with a database for this feature."})


# Serve the static frontend
import os
@app.route("/")
@app.route("/<path:path>")
def serve_frontend(path="index.html"):
    from flask import send_from_directory
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(os.path.join(static_dir, path)):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, "index.html")