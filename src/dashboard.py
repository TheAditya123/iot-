"""Small local dashboard backed directly by the TinyDB event store."""
from __future__ import annotations

from flask import Flask, jsonify, render_template_string

from .config import Config
from .database import EventStore

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <title>Smart Room Monitor</title>
  <style>
    body { font: 16px system-ui, sans-serif; margin: 0 auto; max-width: 1100px; padding: 2rem; color: #17202a; background: #f5f7f9; }
    h1 { margin-bottom: .25rem; } .muted { color: #62707d; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr)); gap: 1rem; margin: 1.5rem 0; }
    .card { background: white; border-radius: .6rem; padding: 1rem; box-shadow: 0 1px 5px #ccd3d8; }
    .value { font-size: 1.65rem; font-weight: 650; margin-top: .4rem; }
    table { width: 100%; border-collapse: collapse; background: white; }
    th, td { text-align: left; padding: .65rem; border-bottom: 1px solid #e4e8eb; }
    th { background: #eaf0f4; } .scroll { overflow-x: auto; }
  </style>
</head>
<body>
  <h1>Smart Room Monitor</h1>
  <div class="muted">PIR-triggered edge AI occupancy history</div>
  {% if current %}
  <div class="cards">
    <div class="card">Motion<div class="value">{{ 'Yes' if current.motion else 'No' }}</div></div>
    <div class="card">People<div class="value">{{ current.people_count if current.people_count is defined else '—' }}</div></div>
  </div>
  <p><strong>Last update:</strong> {{ current.timestamp }}</p>
  {% else %}<p>No real sensor events have been recorded yet.</p>{% endif %}
  <h2>Recent events</h2>
  <div class="scroll"><table>
    <thead><tr><th>Time</th><th>Motion</th><th>People</th><th>Inference</th><th>Image</th><th>Published</th></tr></thead>
    <tbody>{% for row in rows %}<tr>
      <td>{{ row.event.timestamp }}</td>
      <td>{{ 'Yes' if row.event.motion else 'No' }}</td>
      <td>{{ row.event.people_count if row.event.people_count is defined else '—' }}</td>
      <td>{{ row.event.inference_ms ~ ' ms' if row.event.inference_ms is defined else '—' }}</td>
      <td>{{ row.event.image_path if row.event.image_path is defined else '—' }}</td>
      <td>{{ 'Yes' if row.published else 'No' }}</td>
    </tr>{% endfor %}</tbody>
  </table></div>
</body></html>"""


def read_events(data_path, limit=50):
    store = EventStore(data_path)
    try:
        return store.recent(limit)
    finally:
        store.close()


def create_app(data_path):
    app = Flask(__name__)

    @app.get("/")
    def index():
        rows = read_events(data_path)
        current = rows[0]["event"] if rows else None
        return render_template_string(PAGE, current=current, rows=rows)

    @app.get("/api/status")
    def status():
        rows = read_events(data_path, 1)
        return jsonify(rows[0] if rows else None)

    @app.get("/api/events")
    def events():
        return jsonify(read_events(data_path))

    return app


def main():
    config = Config.load(local_only=True)
    create_app(config.data_path).run(
        host=config.dashboard_host, port=config.dashboard_port, debug=False
    )


if __name__ == "__main__":
    main()
