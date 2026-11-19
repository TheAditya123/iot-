"""Tiny filesystem command channel between dashboard and the running monitor."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            json.dump(value, stream, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


class MonitorControl:
    """Single-monitor, multi-request local command queue under ignored data/."""

    def __init__(self, data_dir):
        self.root = Path(data_dir) / "control"
        self.commands = self.root / "commands"
        self.responses = self.root / "responses"
        self.heartbeat = self.root / "heartbeat.json"

    def touch_heartbeat(self):
        _atomic_json(self.heartbeat, {
            "pid": os.getpid(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def is_alive(self, max_age=4.0):
        try:
            age = time.time() - self.heartbeat.stat().st_mtime
        except OSError:
            return False
        return 0 <= age <= max_age

    def submit(self, action, arguments=None):
        request_id = str(uuid.uuid4())
        _atomic_json(self.commands / f"{request_id}.json", {
            "id": request_id,
            "action": action,
            "arguments": arguments or {},
        })
        return request_id

    def consume_next(self):
        self.commands.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.commands.glob("*.json"), key=lambda item: item.stat().st_mtime):
            working = path.with_suffix(".working")
            try:
                path.replace(working)
            except FileNotFoundError:
                continue
            try:
                command = json.loads(working.read_text(encoding="utf-8"))
            finally:
                working.unlink(missing_ok=True)
            if isinstance(command, dict) and command.get("id") and command.get("action"):
                return command
        return None

    def respond(self, request_id, result):
        _atomic_json(self.responses / f"{request_id}.json", {
            "id": request_id,
            "result": result,
        })

    def wait_response(self, request_id, timeout=8.0):
        path = self.responses / f"{request_id}.json"
        deadline = time.monotonic() + max(float(timeout), 0.1)
        while time.monotonic() < deadline:
            if path.exists():
                value = json.loads(path.read_text(encoding="utf-8"))
                path.unlink(missing_ok=True)
                if value.get("id") == request_id:
                    return value.get("result", {})
            time.sleep(0.05)
        raise TimeoutError("The running monitor did not answer the dashboard command in time")

    def request(self, action, arguments=None, timeout=8.0):
        return self.wait_response(
            self.submit(action, arguments),
            timeout=timeout,
        )
