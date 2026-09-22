# Person detection model

The application uses the OpenCV Zoo NanoDet COCO object detector and keeps only
class zero (`person`). It is a small CPU model and does not require TensorFlow,
PyTorch, an AI HAT, or cloud vision.

Download it with:

```bash
.venv/bin/python scripts/download_model.py
```

The downloader writes:

```text
models/object_detection_nanodet_2022nov.onnx
```

It verifies SHA-256
`4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186`
before replacing the destination. ONNX model files are ignored by Git.

Source: [OpenCV Zoo NanoDet](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet),
licensed under Apache 2.0. The model reports COCO object detections. This project
does not perform face recognition or identify people.

Set these values in `.env` after downloading:

```dotenv
INFERENCE_BACKEND=nanodet
MODEL_PATH=models/object_detection_nanodet_2022nov.onnx
PERSON_CONFIDENCE_THRESHOLD=0.5
```

The output includes a measured `people_count`, confidence list, and
`inference_ms`. If the model is missing or cannot load, startup fails clearly;
the application never supplies a simulated count.
