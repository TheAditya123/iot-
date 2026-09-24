"""Run the real local person detector on an existing image."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Config
from src.inference import make_inference


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "image", type=Path,
        help="JPEG or PNG captured locally or supplied as a test image",
    )
    parser.add_argument("--env", help="Optional environment file")
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"Image does not exist: {args.image}")

    config = Config.load(args.env, local_only=True)
    detector = make_inference(config)
    if detector is None:
        parser.error("INFERENCE_BACKEND is off; set it to nanodet")
    try:
        with Image.open(args.image) as image:
            image.load()
            result = detector.predict(image)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Inference test failed: {exc}\n")
    proof = {
        "image": str(args.image.resolve()),
        "image_sha256": sha256(args.image),
        "model": str(config.model_path.resolve()),
        "model_sha256": sha256(config.model_path),
        **result,
    }
    print(json.dumps(proof, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
