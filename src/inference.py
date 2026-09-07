"""Stub plus a single-input/single-output RGB image classifier adapter."""
import importlib
import numpy as np
from PIL import Image


class StubInference:
    def predict(self, image):
        return {"prediction": "unclassified", "confidence": None, "inference_backend": "stub"}


def quantize(values, detail):
    dtype = np.dtype(detail["dtype"])
    if np.issubdtype(dtype, np.floating):
        return values.astype(dtype)
    if dtype not in (np.dtype("int8"), np.dtype("uint8")):
        raise ValueError("Only float, int8 and uint8 model tensors are supported")
    scale, zero = quantization(detail)
    bounds = np.iinfo(dtype)
    return np.clip(np.rint(values / scale + zero), bounds.min, bounds.max).astype(dtype)


def quantization(detail):
    params = detail["quantization_parameters"]
    if len(params["scales"]) != 1 or len(params["zero_points"]) != 1 or params["scales"][0] <= 0:
        raise ValueError("Only positive per-tensor quantization scales are supported")
    return float(params["scales"][0]), int(params["zero_points"][0])


class TFLiteInference:
    def __init__(self, config):
        self.config = config
        if not config.model_path.is_file():
            raise ValueError(f"Missing model: {config.model_path}; see models/README.md")
        interpreter = None
        for module in ("ai_edge_litert.interpreter", "tflite_runtime.interpreter", "tensorflow.lite.python.interpreter"):
            try:
                interpreter = importlib.import_module(module).Interpreter
                break
            except ImportError:
                continue
        if interpreter is None:
            raise RuntimeError("Install a compatible LiteRT, tflite-runtime, or TensorFlow runtime")
        self.engine = interpreter(model_path=str(config.model_path))
        self.engine.allocate_tensors()
        inputs, outputs = self.engine.get_input_details(), self.engine.get_output_details()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("Adapter requires one input and one output; detection models need a different decoder")
        self.input, self.output = inputs[0], outputs[0]
        shape = self.input["shape"]
        if len(shape) != 4 or shape[0] != 1 or shape[3] != 3 or min(shape[1:3]) < 1:
            raise ValueError("Expected fixed input shape [1, height, width, 3]")
        output_shape = self.output["shape"]
        if len(output_shape) != 2 or output_shape[0] != 1 or output_shape[1] < 2:
            raise ValueError("Expected classifier output [1, classes] with at least two classes")
        self.labels = config.labels_path.read_text(encoding="utf-8").splitlines()
        if len(self.labels) != output_shape[1] or any(not label.strip() for label in self.labels):
            raise ValueError("labels.txt must contain exactly one nonempty label per output class")

    def predict(self, image):
        _, height, width, _ = self.input["shape"]
        pixels = np.asarray(image.convert("RGB").resize((int(width), int(height)), Image.Resampling.BILINEAR), dtype=np.float32)
        values = (pixels - self.config.input_mean) / self.config.input_std
        self.engine.set_tensor(self.input["index"], quantize(values[None, ...], self.input))
        self.engine.invoke()
        scores = self.engine.get_tensor(self.output["index"])[0].astype(np.float32)
        if np.issubdtype(self.output["dtype"], np.integer):
            scale, zero = quantization(self.output)
            scores = (scores - zero) * scale
        if not np.all(np.isfinite(scores)):
            raise ValueError("Model produced non-finite output")
        if self.config.output_logits:
            scores = np.exp(scores - scores.max())
            scores /= scores.sum()
        elif np.any(scores < 0) or np.any(scores > 1):
            raise ValueError("Output is not probabilities; check MODEL_OUTPUT_LOGITS and model contract")
        index = int(np.argmax(scores))
        return {"prediction": self.labels[index], "confidence": float(scores[index]), "inference_backend": "tflite"}


def make_inference(config):
    return TFLiteInference(config) if config.inference_backend == "tflite" else StubInference()
