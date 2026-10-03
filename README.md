# Singapore Haze Monitor

Live PSI & PM2.5 monitoring dashboard for Singapore. Fetches data from [data.gov.sg](https://data.gov.sg), stores in SQLite, serves via Flask API, and renders with a responsive dark-themed dashboard.

## Architecture

```
data.gov.sg API → collector.py (cron hourly) → SQLite → api.py (Flask :8095) → haze.html
                                                     ↕ Apache reverse proxy /api/haze/
```

## Components

| File | Purpose |
|---|---|
| `collector.py` | Fetches PSI + PM2.5 hourly, stores in `haze.db` |
| `api.py` | Flask API — `/latest`, `/history`, `/summary` |
| `haze.html` | Browser dashboard with region cards + trend chart |
| `haze-api.service` | systemd unit (auto-start, survives reboot) |

## Quick Start

```bash
# Install deps
uv venv && uv pip install flask requests

# Seed the database
python collector.py

# Start API
python api.py

# Open dashboard
open haze.html
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/haze/latest` | Current PSI + PM2.5 for all 5 regions |
| `GET /api/haze/latest?time=4pm` | Historical reading at specific time |
| `GET /api/haze/history?hours=24&region=central` | Time series |
| `GET /api/haze/summary` | Plain-text summary |

## Regions

central, north, south, east, west

## Bands

**PSI:** Good (0-50) · Moderate (51-100) · Unhealthy (101-200) · Very Unhealthy (201-300) · Hazardous (>300)

**PM2.5:** Band 1 Normal (0-55) · Band 2 Elevated (56-150) · Band 3 High (151-250) · Band 4 Very High (>250)