# Adding an actual model

No trained model is included yet. The planned recognition task is identifying
things such as people and animals in Camera Module 3 photos. Start with a
pre-trained vision model and fine-tune only if its available labels or accuracy
do not meet the project's needs. A text-only small language model cannot
classify a photo; a vision-language model is a different, heavier option.
Inference is off by default, and the application does not generate a prediction
without a real camera image and compatible model. A Pi running TFLite/LiteRT performs edge ML; this is not a
microcontroller TensorFlow Lite Micro deployment.

1. Choose a licensed RGB image **classification** model with one input
   `[1, height, width, 3]` and one output `[1, classes]` (at least two classes).
2. Put it at `models/model.tflite` (ignored by Git) and supply `models/labels.txt`,
   one label per line, in exactly the model's class order.
3. Install `python -m pip install -r requirements-ml.txt` if a LiteRT wheel exists
   for your OS/Python. Otherwise use a supported environment with `tflite-runtime`
   or TensorFlow installed. Those runtimes are loaded only when enabled.
4. Set `INFERENCE_BACKEND=tflite`, `MODEL_PATH` and `LABELS_PATH` in `.env`.
5. Set preprocessing from the model documentation: `MODEL_INPUT_MEAN=0` and
   `MODEL_INPUT_STD=255` map pixels to [0,1]; 127.5/127.5 maps them to [-1,1];
   0/1 preserves [0,255]. This real-valued normalization happens **before**
   int8/uint8 quantization using the model's scale and zero point.
6. Set `MODEL_OUTPUT_LOGITS=true` only when outputs are logits requiring softmax.
   Leave false for probabilities. Check predictions against known labeled images.

The existing adapter returns one label for the **whole image**. A model that
finds multiple people or animals and their positions requires an object
detection adapter; merely dropping a detector file into `models/` will not
work. The adapter supports per-tensor int8/uint8 quantization and floating-point
tensors. It rejects incompatible shapes instead of guessing. Object detection,
binary sigmoid outputs, segmentation, multiple inputs/outputs, per-axis I/O
quantization, model-specific crops and letterboxing require another adapter.

Interpreter reference: https://ai.google.dev/edge/api/tflite/python/tf/lite/Interpreter
