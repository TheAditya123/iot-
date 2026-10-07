"""Create a short human-readable sentence from a real edge-AI event."""
from __future__ import annotations


def summarize_event(event: dict[str, object]) -> str:
    """Describe only facts already measured by the PIR, camera, and detector."""
    if "capture_error" in event:
        return "Motion was detected, but the camera image could not be captured."
    if "image_path" not in event:
        return "Motion was detected, but no camera image was captured."
    if "inference_error" in event:
        return "Motion and a camera image were recorded, but person detection failed."

    count = event.get("people_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return "Motion and a camera image were recorded; person detection was not enabled."
    if count == 0:
        return "Motion was detected, but no person was detected in the camera image."
    if count == 1:
        return "Motion was detected and one person was detected in the camera image."
    return f"Motion was detected and {count} people were detected in the camera image."
