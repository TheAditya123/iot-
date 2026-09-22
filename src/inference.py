"""Local CPU person detection for event-triggered camera images."""
from __future__ import annotations

import time

import numpy as np
from PIL import Image


def letterbox(image: np.ndarray, size: int = 416) -> np.ndarray:
    """Resize an RGB image to a square without changing its aspect ratio."""
    import cv2

    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    resized = cv2.resize(image, (round(width * scale), round(height * scale)),
                         interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    top = (size - resized.shape[0]) // 2
    left = (size - resized.shape[1]) // 2
    canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
    return canvas


class NanoDetPersonDetector:
    """Count COCO class 0 (person) using OpenCV Zoo NanoDet."""

    SIZE = 416
    STRIDES = (8, 16, 32, 64)
    REG_MAX = 7

    def __init__(self, config):
        import cv2

        if not config.model_path.is_file():
            raise ValueError(
                f"Missing detector model: {config.model_path}; run scripts/download_model.py"
            )
        self.cv2 = cv2
        self.threshold = config.person_confidence_threshold
        self.nms_threshold = 0.6
        self.project = np.arange(self.REG_MAX + 1)
        self.mean = np.array([103.53, 116.28, 123.675], dtype=np.float32).reshape(1, 1, 3)
        self.std = np.array([57.375, 57.12, 58.395], dtype=np.float32).reshape(1, 1, 3)
        self.anchors = []
        for stride in self.STRIDES:
            count = self.SIZE // stride
            x, y = np.meshgrid(np.arange(count) * stride, np.arange(count) * stride)
            self.anchors.append(np.column_stack(
                (x.ravel() + 0.5 * (stride - 1), y.ravel() + 0.5 * (stride - 1))
            ))
        self.net = cv2.dnn.readNet(str(config.model_path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    def _decode(self, outputs) -> tuple[list[list[float]], list[float]]:
        boxes, scores = [], []
        for stride, class_scores, box_values, anchors in zip(
                self.STRIDES, outputs[::2], outputs[1::2], self.anchors):
            class_scores = np.squeeze(class_scores, axis=0) if class_scores.ndim == 3 else class_scores
            box_values = np.squeeze(box_values, axis=0) if box_values.ndim == 3 else box_values
            person_scores = class_scores[:, 0]
            keep = person_scores >= self.threshold
            if not np.any(keep):
                continue
            person_scores = person_scores[keep]
            anchors = anchors[keep]
            distances = box_values[keep].reshape(-1, self.REG_MAX + 1)
            distances -= distances.max(axis=1, keepdims=True)
            distances = np.exp(distances)
            distances /= distances.sum(axis=1, keepdims=True)
            distances = np.dot(distances, self.project).reshape(-1, 4) * stride
            x1 = np.clip(anchors[:, 0] - distances[:, 0], 0, self.SIZE)
            y1 = np.clip(anchors[:, 1] - distances[:, 1], 0, self.SIZE)
            x2 = np.clip(anchors[:, 0] + distances[:, 2], 0, self.SIZE)
            y2 = np.clip(anchors[:, 1] + distances[:, 3], 0, self.SIZE)
            boxes.extend(np.column_stack((x1, y1, x2 - x1, y2 - y1)).tolist())
            scores.extend(person_scores.astype(float).tolist())
        return boxes, scores

    def predict(self, image: Image.Image) -> dict[str, object]:
        started = time.perf_counter()
        rgb = letterbox(np.asarray(image.convert("RGB")), self.SIZE)
        normalized = (rgb.astype(np.float32) - self.mean) / self.std
        self.net.setInput(self.cv2.dnn.blobFromImage(normalized))
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        boxes, scores = self._decode(outputs)
        indices = self.cv2.dnn.NMSBoxes(
            boxes, scores, self.threshold, self.nms_threshold
        ) if boxes else []
        selected = [scores[int(index)] for index in np.asarray(indices).reshape(-1)]
        return {
            "people_count": len(selected),
            "person_confidences": [round(value, 3) for value in sorted(selected, reverse=True)],
            "inference_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "inference_backend": "nanodet",
        }


def make_inference(config):
    return NanoDetPersonDetector(config) if config.inference_backend == "nanodet" else None
