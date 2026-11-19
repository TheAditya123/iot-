"""Local Smart Room dashboard plus bounded operations-agent API."""
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from .config import Config
from .database import EventStore
from .operations_agent import OperationsAgent, OperationsTools

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
    return {
        "total_events": len(rows),
        "published_events": published,
        "pending_events": len(rows) - published,
        "max_people": max(people) if people else None,
        "average_people": round(sum(people) / len(people), 2) if people else None,
        "average_inference_ms": (
            round(sum(latencies) / len(latencies), 1) if latencies else None
        ),
        "latest_timestamp": events[0].get("timestamp") if events else None,
    }


def create_app(data_path, agent=None):
    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )
    agent = agent or OperationsAgent(OperationsTools(data_path))

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
        return jsonify(read_events(data_path, min(max(limit, 1), 200)))

    @app.get("/api/metrics")
    def metrics():
        return jsonify(metrics_for_rows(read_events(data_path, 500)))

    @app.get("/api/agent/status")
    def agent_status():
        return jsonify(agent.status())

    @app.post("/api/agent/chat")
    def agent_chat():
        body = request.get_json(silent=True) or {}
        history = body.get("history", [])
        if history is not None and not isinstance(history, list):
            return jsonify({"error": "history must be a list"}), 400
        try:
            return jsonify(agent.chat(body.get("message"), history or []))
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            app.logger.exception("Operations agent action failed")
            return jsonify(
                {"error": "Agent action failed; check the Pi logs for details."}
            ), 500

    @app.get("/api/agent/report.csv")
    def agent_report():
        window = request.args.get("window", "today")
        if window not in {"today", "yesterday", "all"}:
            return jsonify(
                {"error": "window must be today, yesterday, or all"}
            ), 400
        try:
            content, count = agent.tools.report_csv(window)
        except Exception:
            app.logger.exception("Report generation failed")
            return jsonify({"error": "Could not build the report."}), 500
        return Response(
            content,
            mimetype="text/csv",
            headers={
                "Content-Disposition":
                    f'attachment; filename="smart-room-{window}-{count}-events.csv"'
            },
        )

    return app


def main():
    config = Config.load(local_only=True)
    create_app(config.data_path).run(
        host=config.dashboard_host,
        port=config.dashboard_port,
        debug=False,
    )


if __name__ == "__main__":
    main()
