# Local frontend

The repository includes a responsive local Smart Room dashboard served by Flask.
It reads the real TinyDB event history directly, so the UI continues to work
even if AWS is offline.

## Run

```bash
cd ~/iot-project
.venv/bin/python -m src.dashboard
```

Open on the Pi:

```text
http://localhost:5000
```

Or from another device on the same LAN:

```text
http://raspberrypi.local:5000
```

The host and port can be changed with `DASHBOARD_HOST` and
`DASHBOARD_PORT` in `.env`.

## Dashboard features

- current PIR motion event
- current person count from the local NanoDet detector
- BME280-ready temperature, humidity, and pressure cards (show `N/A` until
  those fields exist in real events)
- cloud/outbox sync state
- recent event table
- occupancy-over-time chart
- current and average inference latency
- peak observed occupancy
- device ID, PIR GPIO, and inference backend
- explicit local-first pipeline view

No sensor value is fabricated. Missing fields render as `N/A`.

## API

| Route | Purpose |
|---|---|
| `/` | Dashboard |
| `/api/status` | Latest TinyDB row |
| `/api/events?limit=50` | Recent TinyDB rows (1-200) |
| `/api/metrics` | Local aggregate metrics from recent history |

Static frontend files live in `static/`; the page template is
`templates/dashboard.html`.
