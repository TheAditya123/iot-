"""Bounded Smart Room operations agent for the local dashboard.

An optional LLM chooses one explicit tool. Tool execution and all sensor/system
facts remain deterministic local Python code. Without an API key, a small local
planner supports the prepared classroom demo prompts.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import uuid

from dotenv import dotenv_values

from .camera import make_camera, save_jpeg_atomic
from .config import Config
from .database import EventStore
from .inference import make_inference
from .monitor_control import MonitorControl
from .runtime_settings import get_person_threshold, set_person_threshold

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = {"last_hour", "today", "yesterday", "all"}


def _parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def _window_label(window):
    return {
        "last_hour": "the last hour",
        "today": "today",
        "yesterday": "yesterday",
        "all": "recorded history",
    }[window]


class OperationsTools:
    def __init__(self, data_path, root=ROOT, now_fn=None):
        self.data_path = Path(data_path)
        self.root = Path(root)
        self.now_fn = now_fn or (lambda: datetime.now().astimezone())
        self.runtime_settings_path = self.root / "data" / "runtime-settings.json"

    def _rows(self, limit=5000):
        store = EventStore(self.data_path)
        try:
            return store.recent(limit)
        finally:
            store.close()

    def _filter_window(self, rows, window):
        if window not in WINDOWS:
            raise ValueError(f"Unsupported time window: {window}")
        if window == "all":
            return rows
        now = self.now_fn()
        if now.tzinfo is None:
            now = now.astimezone()
        if window == "last_hour":
            start = now - timedelta(hours=1)
            return [
                row for row in rows
                if (stamp := _parse_timestamp(row.get("event", {}).get("timestamp")))
                and start <= stamp <= now
            ]
        target_date = now.date() if window == "today" else (now - timedelta(days=1)).date()
        return [
            row for row in rows
            if (stamp := _parse_timestamp(row.get("event", {}).get("timestamp")))
            and stamp.astimezone(now.tzinfo).date() == target_date
        ]

    def current_status(self):
        rows = self._rows(1)
        config = Config.load(local_only=True)
        threshold = get_person_threshold(
            config.person_confidence_threshold, self.runtime_settings_path
        )
        return {
            "latest": rows[0] if rows else None,
            "threshold": threshold,
            "device_id": config.device_id,
        }

    def occupancy_summary(self, window="last_hour"):
        rows = self._filter_window(self._rows(), window)
        counts = [
            int(row.get("event", {}).get("people_count", 0))
            for row in rows
            if isinstance(row.get("event", {}).get("people_count"), (int, float))
        ]
        return {
            "window": window,
            "event_count": len(rows),
            "events_with_people": sum(1 for value in counts if value > 0),
            "detection_sum": sum(counts),
            "average_people": round(sum(counts) / len(counts), 2) if counts else 0,
            "peak_people": max(counts) if counts else 0,
        }

    def compare_days(self):
        today = self.occupancy_summary("today")
        yesterday = self.occupancy_summary("yesterday")
        return {
            "today": today,
            "yesterday": yesterday,
            "detection_delta": today["detection_sum"] - yesterday["detection_sum"],
        }

    def events_with_people(self, min_people=1, window="all"):
        min_people = int(min_people)
        if not 1 <= min_people <= 100:
            raise ValueError("min_people must be between 1 and 100")
        rows = self._filter_window(self._rows(), window)
        matches = [
            row for row in rows
            if isinstance(row.get("event", {}).get("people_count"), (int, float))
            and row["event"]["people_count"] >= min_people
        ]
        return {
            "window": window,
            "min_people": min_people,
            "count": len(matches),
            "events": matches[:20],
        }

    def _cloud_config(self):
        values = {**dotenv_values(self.root / ".env.aws"), **os.environ}

        def value(name, default=""):
            return str(values.get(name) or default).strip()

        def path(name, default):
            candidate = Path(value(name, default)).expanduser()
            return candidate if candidate.is_absolute() else self.root / candidate

        return {
            "endpoint": value("MQTT_ENDPOINT"),
            "port": int(value("MQTT_PORT", "8883")),
            "ca": path("MQTT_CA_CERT", "certs/AmazonRootCA1.pem"),
            "cert": path("MQTT_CLIENT_CERT", "certs/device.cert.pem"),
            "key": path("MQTT_PRIVATE_KEY", "certs/device.private.key"),
        }

    def cloud_health(self):
        rows = self._rows()
        pending = sum(1 for row in rows if not row.get("published"))
        config = self._cloud_config()
        missing = [
            label for label, path in (
                ("CA certificate", config["ca"]),
                ("device certificate", config["cert"]),
                ("private key", config["key"]),
            ) if not path.is_file()
        ]
        if not config["endpoint"]:
            return {
                "configured": False,
                "tls_reachable": False,
                "pending_events": pending,
                "detail": "MQTT_ENDPOINT is not configured in .env.aws.",
            }
        if missing:
            return {
                "configured": False,
                "tls_reachable": False,
                "pending_events": pending,
                "detail": "Missing " + ", ".join(missing) + ".",
            }
        try:
            context = ssl.create_default_context(cafile=str(config["ca"]))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(config["cert"]), str(config["key"]))
            with socket.create_connection(
                (config["endpoint"], config["port"]), timeout=2.5
            ) as raw:
                with context.wrap_socket(raw, server_hostname=config["endpoint"]):
                    pass
        except (OSError, ssl.SSLError) as exc:
            return {
                "configured": True,
                "tls_reachable": False,
                "pending_events": pending,
                "detail": f"AWS IoT TLS handshake failed: {exc}",
            }
        return {
            "configured": True,
            "tls_reachable": True,
            "pending_events": pending,
            "detail": "AWS IoT TLS handshake succeeded with the configured device certificate.",
        }

    def sensor_health(self):
        rows = self._rows()
        latest_motion = next(
            (
                row.get("event", {}).get("timestamp")
                for row in rows
                if row.get("event", {}).get("motion") is True
            ),
            None,
        )
        camera = {
            "status": "unknown",
            "detail": "rpicam-hello is not available on this host.",
        }
        command = shutil.which("rpicam-hello")
        if command:
            try:
                result = subprocess.run(
                    [command, "--list-cameras"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                output = (result.stdout + "\n" + result.stderr).strip()
                lowered = output.lower()
                if (
                    result.returncode == 0
                    and "available cameras" in lowered
                    and "no cameras" not in lowered
                ):
                    camera = {
                        "status": "ok",
                        "detail": "Raspberry Pi camera is enumerated.",
                    }
                else:
                    camera = {
                        "status": "warning",
                        "detail": output[-500:] or "Camera enumeration returned no camera.",
                    }
            except (OSError, subprocess.TimeoutExpired) as exc:
                camera = {
                    "status": "warning",
                    "detail": f"Camera probe failed: {exc}",
                }

        config = Config.load(local_only=True)
        model = {
            "status": "ok" if config.model_path.is_file() else "warning",
            "detail": (
                f"NanoDet model present at {config.model_path}"
                if config.model_path.is_file()
                else f"NanoDet model missing at {config.model_path}"
            ),
        }
        pir = {
            "status": "ok" if latest_motion else "unknown",
            "detail": (
                f"Last recorded real PIR motion event: {latest_motion}"
                if latest_motion
                else f"PIR is configured on BCM GPIO{config.gpio}, but there is no recorded motion event yet."
            ),
        }
        return {
            "camera": camera,
            "pir": pir,
            "model": model,
            "bme280": {
                "status": "not_installed",
                "detail": "BME280 is planned but is not current production hardware yet.",
            },
            "cloud": self.cloud_health(),
        }

    def capture_and_infer(self):
        # Prefer the long-running monitor because it already owns Picamera2.
        # This avoids stopping the sensor pipeline just to demo an agent action.
        control = MonitorControl(self.data_path.parent)
        if control.is_alive():
            try:
                return control.request("capture_and_infer", timeout=8.0)
            except TimeoutError as exc:
                return {
                    "ok": False,
                    "detail": f"The monitor is running but did not answer the capture request: {exc}",
                }

        # If the monitor is not running, the dashboard can safely own the camera.
        config = Config.load(local_only=True)
        if config.camera_backend == "off":
            return {"ok": False, "detail": "Camera is disabled by CAMERA_BACKEND=off."}
        if not config.model_path.is_file():
            return {"ok": False, "detail": f"NanoDet model is missing at {config.model_path}."}
        if config.inference_backend != "nanodet":
            config = replace(config, inference_backend="nanodet")

        camera = None
        try:
            camera = make_camera(config)
            detector = make_inference(config)
            image = camera.capture()
            image_path = config.image_dir / f"agent-{uuid.uuid4()}.jpg"
            save_jpeg_atomic(image, image_path)
            result = detector.predict(image)
            return {"ok": True, "image_path": str(image_path), **result}
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"Direct camera capture/inference failed: {exc}",
            }
        finally:
            if camera is not None:
                camera.close()

    def set_threshold(self, value):
        config = Config.load(local_only=True)
        previous = get_person_threshold(
            config.person_confidence_threshold, self.runtime_settings_path
        )
        current = set_person_threshold(value, self.runtime_settings_path)
        return {"previous": previous, "current": current}

    def report_csv(self, window="today"):
        rows = self._filter_window(self._rows(), window)
        output = io.StringIO()
        fields = [
            "timestamp", "device_id", "event_id", "motion", "people_count",
            "temperature_c", "humidity_pct", "pressure_hpa",
            "inference_ms", "summary", "published",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in reversed(rows):
            event = row.get("event", {})
            writer.writerow({
                "timestamp": event.get("timestamp", ""),
                "device_id": event.get("device_id", ""),
                "event_id": event.get("event_id", ""),
                "motion": event.get("motion", ""),
                "people_count": event.get("people_count", ""),
                "temperature_c": event.get("temperature_c", ""),
                "humidity_pct": event.get("humidity_pct", ""),
                "pressure_hpa": event.get("pressure_hpa", ""),
                "inference_ms": event.get("inference_ms", ""),
                "summary": event.get("summary", ""),
                "published": row.get("published", False),
            })
        return output.getvalue(), len(rows)

    def execute(self, tool, arguments):
        arguments = arguments or {}
        if tool == "current_status":
            return self.current_status()
        if tool == "occupancy_summary":
            return self.occupancy_summary(str(arguments.get("window", "last_hour")))
        if tool == "compare_days":
            return self.compare_days()
        if tool == "events_with_people":
            return self.events_with_people(
                int(arguments.get("min_people", 1)),
                str(arguments.get("window", "all")),
            )
        if tool == "sensor_health":
            return self.sensor_health()
        if tool == "cloud_health":
            return self.cloud_health()
        if tool == "capture_and_infer":
            return self.capture_and_infer()
        if tool == "set_threshold":
            return self.set_threshold(float(arguments["value"]))
        if tool == "export_report":
            window = str(arguments.get("window", "today"))
            if window not in {"today", "yesterday", "all"}:
                raise ValueError("Reports support today, yesterday, or all")
            return {
                "window": window,
                "download_url": f"/api/agent/report.csv?window={window}",
            }
        raise ValueError(f"Unsupported agent tool: {tool}")


TOOL_CATALOG = {
    "current_status": {},
    "occupancy_summary": {"window": "last_hour|today|yesterday|all"},
    "compare_days": {},
    "events_with_people": {
        "min_people": "integer 1-100",
        "window": "last_hour|today|yesterday|all",
    },
    "sensor_health": {},
    "cloud_health": {},
    "capture_and_infer": {},
    "set_threshold": {"value": "number 0.20-0.90"},
    "export_report": {"window": "today|yesterday|all"},
}


def fallback_plan(message):
    text = message.lower().strip()
    threshold = re.search(
        r"(?:threshold|confidence)(?:\s+to|\s*=|\s+at)?\s*(0(?:\.\d+)?|1(?:\.0+)?)",
        text,
    )
    if threshold:
        return {
            "tool": "set_threshold",
            "arguments": {"value": float(threshold.group(1))},
        }
    if "export" in text or (
        "report" in text
        and any(word in text for word in ("csv", "download", "today", "yesterday"))
    ):
        window = "yesterday" if "yesterday" in text else "all" if "all" in text else "today"
        return {"tool": "export_report", "arguments": {"window": window}}
    if (
        ("capture" in text or "take" in text)
        and ("image" in text or "photo" in text or "infer" in text)
    ) or "infer now" in text or "run inference" in text:
        return {"tool": "capture_and_infer", "arguments": {}}
    if (
        ("aws" in text or "cloud" in text or "mqtt" in text)
        and any(word in text for word in ("offline", "health", "status", "why"))
    ):
        return {"tool": "cloud_health", "arguments": {}}
    if "sensor health" in text or "diagnostic" in text or "diagnostics" in text:
        return {"tool": "sensor_health", "arguments": {}}
    if "busier" in text or ("today" in text and "yesterday" in text):
        return {"tool": "compare_days", "arguments": {}}

    more = re.search(
        r"(?:more than|over|>)\s*(\d+)\s*(?:people|persons?|occupants?)",
        text,
    )
    at_least = re.search(
        r"(?:at least|>=)\s*(\d+)\s*(?:people|persons?|occupants?)",
        text,
    )
    match = more or at_least
    if match:
        number = int(match.group(1)) + (1 if more else 0)
        window = (
            "last_hour" if "last hour" in text
            else "today" if "today" in text
            else "yesterday" if "yesterday" in text
            else "all"
        )
        return {
            "tool": "events_with_people",
            "arguments": {"min_people": number, "window": window},
        }

    window = (
        "last_hour" if "last hour" in text or "past hour" in text
        else "yesterday" if "yesterday" in text
        else "today" if "today" in text
        else None
    )
    if window and any(
        word in text for word in ("people", "busy", "occupancy", "events", "detected", "how many")
    ):
        return {"tool": "occupancy_summary", "arguments": {"window": window}}
    if "health" in text or "sensor" in text:
        return {"tool": "sensor_health", "arguments": {}}
    return {"tool": "current_status", "arguments": {}}


class OpenAIPlanner:
    def __init__(self, root=ROOT):
        values = {**dotenv_values(Path(root) / ".env.agent"), **os.environ}
        self.api_key = str(values.get("OPENAI_API_KEY") or "").strip()
        self.model = str(values.get("OPENAI_MODEL") or "gpt-6-luna").strip()

    @property
    def available(self):
        return bool(self.api_key)

    def plan(self, message, history=None):
        if not self.available:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        from openai import OpenAI

        history_text = ""
        if history:
            safe = history[-6:]
            history_text = "\nRecent dashboard conversation:\n" + "\n".join(
                f"{item.get('role', 'user')}: {str(item.get('content', ''))[:300]}"
                for item in safe if isinstance(item, dict)
            )
        system = f"""You are the planner for a Raspberry Pi Smart Room operations agent.
Choose exactly ONE tool from this catalog:
{json.dumps(TOOL_CATALOG, separators=(',', ':'))}

Return ONLY JSON:
{{"tool":"tool_name","arguments":{{...}}}}

Rules:
- Never invent sensor values or answer from memory.
- Use tools for every factual answer.
- "more than 3 people" means min_people=4.
- Use compare_days for today-vs-yesterday questions.
- Use cloud_health for AWS/MQTT diagnosis.
- Use sensor_health for general diagnostics.
- Use capture_and_infer only when explicitly asked for a new capture.
- Use set_threshold only when explicitly asked for a numeric threshold.
- Never request shell/code/file tools; none exist.
{history_text}"""
        response = OpenAI(api_key=self.api_key).responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
        )
        text = response.output_text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Planner did not return JSON")
        plan = json.loads(text[start:end + 1])
        if plan.get("tool") not in TOOL_CATALOG:
            raise ValueError("Planner selected an unsupported tool")
        if not isinstance(plan.get("arguments", {}), dict):
            raise ValueError("Planner arguments must be an object")
        return {"tool": plan["tool"], "arguments": plan.get("arguments", {})}


def format_answer(tool, result):
    if tool == "current_status":
        latest = result["latest"]
        if not latest:
            return (
                "No real sensor events have been recorded yet. "
                f"The current person-confidence threshold is {result['threshold']:.2f}."
            )
        event = latest.get("event", {})
        people = event.get("people_count", "not measured")
        return (
            f"Latest event: motion={bool(event.get('motion'))}, people={people}, "
            f"time={event.get('timestamp', 'unknown')}. "
            f"Cloud outbox state: {'synced' if latest.get('published') else 'pending'}. "
            f"Current detection threshold: {result['threshold']:.2f}."
        )
    if tool == "occupancy_summary":
        return (
            f"For {_window_label(result['window'])}: {result['event_count']} events, "
            f"{result['detection_sum']} total person detections across those events, "
            f"{result['events_with_people']} events with at least one person, "
            f"and peak observed occupancy {result['peak_people']}."
        )
    if tool == "compare_days":
        today, yesterday = result["today"], result["yesterday"]
        delta = result["detection_delta"]
        comparison = (
            f"Today is busier by {delta} summed person detections."
            if delta > 0
            else f"Yesterday was busier by {-delta} summed person detections."
            if delta < 0
            else "They are tied by summed person detections."
        )
        return (
            f"Today: {today['event_count']} events / {today['detection_sum']} detections. "
            f"Yesterday: {yesterday['event_count']} events / {yesterday['detection_sum']} detections. "
            + comparison
        )
    if tool == "events_with_people":
        if not result["count"]:
            return (
                f"No events in {_window_label(result['window'])} had "
                f"{result['min_people']} or more people."
            )
        sample = ", ".join(
            f"{row['event'].get('timestamp', '?')} ({row['event'].get('people_count')} people)"
            for row in result["events"][:5]
        )
        return (
            f"Found {result['count']} events in {_window_label(result['window'])} with "
            f"{result['min_people']} or more people. First matches: {sample}."
        )
    if tool == "cloud_health":
        return (
            f"AWS IoT is {'reachable' if result['tls_reachable'] else 'not reachable'}. "
            f"{result['detail']} Local TinyDB has {result['pending_events']} pending event(s)."
        )
    if tool == "sensor_health":
        parts = []
        for name in ("camera", "pir", "model", "bme280"):
            item = result[name]
            parts.append(f"{name.upper()}: {item['status']} — {item['detail']}")
        cloud = result["cloud"]
        parts.append(
            f"AWS: {'ok' if cloud['tls_reachable'] else 'warning'} — "
            f"{cloud['detail']} ({cloud['pending_events']} pending)"
        )
        return " | ".join(parts)
    if tool == "capture_and_infer":
        if not result.get("ok"):
            return result.get("detail", "Manual capture/inference failed.")
        return (
            f"Captured a fresh image and ran NanoDet locally: "
            f"{result['people_count']} people detected in {result['inference_ms']} ms "
            f"at threshold {result.get('person_confidence_threshold', 'current')}. "
            f"Image saved locally at {result['image_path']}."
        )
    if tool == "set_threshold":
        return (
            f"Detection threshold changed from {result['previous']:.2f} to "
            f"{result['current']:.2f}. It applies to the next inference without restarting."
        )
    if tool == "export_report":
        return (
            f"Your {_window_label(result['window'])} occupancy CSV is ready. "
            "Use the download button below."
        )
    return "Tool completed."


class OperationsAgent:
    def __init__(self, tools, planner=None):
        self.tools = tools
        self.planner = planner or OpenAIPlanner(tools.root)

    def status(self):
        return {
            "mode": "openai-responses" if self.planner.available else "local-demo-planner",
            "model": self.planner.model if self.planner.available else None,
            "tools": list(TOOL_CATALOG),
            "note": (
                "LLM chooses a bounded tool; local Python validates and executes it."
                if self.planner.available
                else "No API key configured; deterministic demo planner is active."
            ),
        }

    def chat(self, message, history=None):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message is required")
        if len(message) > 500:
            raise ValueError("message must be 500 characters or fewer")
        source = "local-demo-planner"
        try:
            if self.planner.available:
                plan = self.planner.plan(message.strip(), history)
                source = "openai-responses"
            else:
                plan = fallback_plan(message)
        except Exception:
            plan = fallback_plan(message)
            source = "local-fallback-after-ai-error"
        result = self.tools.execute(plan["tool"], plan.get("arguments", {}))
        response = {
            "answer": format_answer(plan["tool"], result),
            "tool": plan["tool"],
            "arguments": plan.get("arguments", {}),
            "planner": source,
        }
        if isinstance(result, dict) and result.get("download_url"):
            response["download_url"] = result["download_url"]
        return response
