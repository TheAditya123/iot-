"""Real Raspberry Pi CSI camera and optional USB webcam adapters."""
import logging
import os
from pathlib import Path
import time
from PIL import Image

LOG = logging.getLogger(__name__)


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
                detail = (
                    "Raspberry Pi OS found zero CSI camera sensors; the camera stack is "
                    "installed, so power off and check the ribbon orientation, both "
                    "latches, the Pi 5 22-pin cable, and the other CAM/DISP port"
                )
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
