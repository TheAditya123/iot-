"""Local Smart Room web dashboard backed by the TinyDB event store."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .config import Config
from .database import EventStore

ROOT = Path(__file__).resolve().parents[1]


def read_events(data_path, limit=50):
    store = EventStore(data_path)
    try:
        return store.recent(limit)
    finally:
        store.close()


def metrics_for_rows(rows):
    events = [row.get("event", {}) for row in rows]
    people = [
        event.get("people_count")
        for event in events
        if isinstance(event.get("people_count"), (int, float))
    ]
    latencies = [
        event.get("inference_ms")
        for event in events
        if isinstance(event.get("inference_ms"), (int, float))
    ]
    published = sum(1 for row in rows if row.get("published"))
    pending = len(rows) - published

    return {
        "total_events": len(rows),
        "published_events": published,
        "pending_events": pending,
        "max_people": max(people) if people else None,
        "average_people": round(sum(people) / len(people), 2) if people else None,
        "average_inference_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "latest_timestamp": events[0].get("timestamp") if events else None,
    }


def create_app(data_path):
    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )

    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/api/status")
    def status():
        rows = read_events(data_path, 1)
        return jsonify(rows[0] if rows else None)

    @app.get("/api/events")
    def events():
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            limit = 50
        limit = min(max(limit, 1), 200)
        return jsonify(read_events(data_path, limit))

    @app.get("/api/metrics")
    def metrics():
        return jsonify(metrics_for_rows(read_events(data_path, 500)))

    return app


def main():
    config = Config.load(local_only=True)
    create_app(config.data_path).run(
        host=config.dashboard_host, port=config.dashboard_port, debug=False
    )


if __name__ == "__main__":
    main()
