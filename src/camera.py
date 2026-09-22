"""Real Pi Camera Module 3 and optional USB webcam adapters."""
import logging
import time
from PIL import Image

LOG = logging.getLogger(__name__)


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
            raise RuntimeError(
                "No usable Pi camera detected; check rpicam-hello --list-cameras and reseat the ribbon"
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
