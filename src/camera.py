"""Optional real camera adapters. Capture is off until configured."""
import time
from PIL import Image


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
        self.camera = Picamera2()
        try:
            # Picamera2 BGR888 produces RGB byte order for Pillow.
            self.camera.configure(self.camera.create_still_configuration(
                main={"size": (640, 480), "format": "BGR888"}))
            self.camera.start()
            time.sleep(2)
        except BaseException:
            self.camera.close()
            raise

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
