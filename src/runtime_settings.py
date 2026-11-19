"""Atomic runtime settings used by the Smart Room operations agent."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "runtime-settings.json"
MIN_PERSON_THRESHOLD = 0.20
MAX_PERSON_THRESHOLD = 0.90


def _validate_threshold(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not MIN_PERSON_THRESHOLD <= value <= MAX_PERSON_THRESHOLD:
        raise ValueError(
            f"Person confidence threshold must be between "
            f"{MIN_PERSON_THRESHOLD:.2f} and {MAX_PERSON_THRESHOLD:.2f}"
        )
    return round(value, 3)


def read_settings(path: Path = DEFAULT_PATH) -> dict:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {}
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    return value if isinstance(value, dict) else {}


def get_person_threshold(default: float, path: Path = DEFAULT_PATH) -> float:
    settings = read_settings(path)
    try:
        return _validate_threshold(settings.get("person_confidence_threshold", default))
    except (TypeError, ValueError):
        return _validate_threshold(default)


def set_person_threshold(value: float, path: Path = DEFAULT_PATH) -> float:
    value = _validate_threshold(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = read_settings(path)
    settings["person_confidence_threshold"] = value
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            json.dump(settings, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return value
