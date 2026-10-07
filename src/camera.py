"""Real Raspberry Pi CSI camera and optional USB webcam adapters."""
import logging
import os
from pathlib import Path
import subprocess
import time
from PIL import Image

LOG = logging.getLogger(__name__)


def read_camera_kernel_log() -> str:
    """Return current-boot camera probe messages when the journal is readable."""
    try:
        result = subprocess.run(
            ["journalctl", "-k", "-b", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def explain_no_csi_camera(kernel_log: str = "") -> str:
    """Turn kernel probe evidence into an actionable camera diagnosis."""
    lowered = kernel_log.lower()
    imx708_id_failed = "imx708" in lowered and "failed to read chip id" in lowered
    autofocus_failed = ("dw9807" in lowered or "dw9817" in lowered) and "i2c" in lowered
    if imx708_id_failed and autofocus_failed:
        return (
            "The correct IMX708 driver loaded, but the sensor chip-ID read failed "
            "and the autofocus controller also failed I2C communication. The Pi is "
            "not electrically communicating with the Camera Module 3; test a known-good "
            "Raspberry Pi 15-to-22-pin Standard-to-Mini ribbon, then the camera board"
        )
    if "failed to read chip id" in lowered or "probe with driver" in lowered:
        return (
            "A CSI sensor driver loaded, but its hardware probe failed. Check the "
            "ribbon contacts and latches, then test a known-good Pi 5 camera cable"
        )
    return (
        "Raspberry Pi OS found zero CSI camera sensors. Check the ribbon orientation, "
        "both latches, the Pi 5 22-pin cable, and the selected CAM/DISP connector"
    )


def save_jpeg_atomic(image: Image.Image, path: Path) -> None:
    """Durably replace a JPEG without exposing a partial capture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        image.save(temporary, "JPEG")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class WebcamCamera:
    def __init__(self, index):
        import cv2
        self.cv2 = cv2
        self.camera = cv2.VideoCapture(index)
        if not self.camera.isOpened():
            self.camera.release()
            raise RuntimeError(f"Cannot open webcam {index}; check OS camera access")

    def capture(self):
        # Drain buffered frames so captures reflect the latest scene.
        for _ in range(4):
            self.camera.grab()
        ok, frame = self.camera.read()
        if not ok:
            raise RuntimeError("Webcam capture failed")
        return Image.fromarray(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB))

    def close(self):
        self.camera.release()


class PiCamera:
    def __init__(self):
        from picamera2 import Picamera2
        from libcamera import controls
        self.camera = None
        try:
            self.camera = Picamera2()
            # Picamera2 BGR888 produces RGB byte order for Pillow.
            self.camera.configure(self.camera.create_still_configuration(
                main={"size": (1280, 960), "format": "BGR888"}))
            self.camera.start()
            model = self.camera.camera_properties.get("Model", "unknown")
            if "AfMode" in self.camera.camera_controls:
                self.camera.set_controls({"AfMode": controls.AfModeEnum.Continuous})
                LOG.info("Pi camera detected: %s (continuous autofocus enabled)", model)
            else:
                LOG.info("Pi camera detected: %s (fixed focus)", model)
            time.sleep(2)
        except Exception as exc:
            if self.camera is not None:
                self.camera.close()
            if "No camera number" in str(exc):
                detail = explain_no_csi_camera(read_camera_kernel_log())
            else:
                detail = (
                    "Picamera2 initialization failed; inspect the underlying exception "
                    "and rpicam-hello --list-cameras"
                )
            raise RuntimeError(
                detail
            ) from exc

    def capture(self):
        return self.camera.capture_image("main").convert("RGB")

    def close(self):
        self.camera.close()


def make_camera(config):
    if config.camera_backend == "off":
        return None
    if config.camera_backend == "webcam":
        return WebcamCamera(config.webcam_index)
    return PiCamera()
