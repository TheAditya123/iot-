"""Download and verify the small OpenCV Zoo NanoDet model."""
from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "models" / "object_detection_nanodet_2022nov.onnx"
URL = ("https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/"
       "object_detection_nanodet/object_detection_nanodet_2022nov.onnx")
SHA256 = "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186"


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def main():
    if DESTINATION.is_file() and digest(DESTINATION) == SHA256:
        print(f"Model already verified: {DESTINATION}")
        return
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    temporary = DESTINATION.with_suffix(".onnx.download")
    try:
        print(f"Downloading {URL}")
        with urllib.request.urlopen(URL, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = digest(temporary)
        if actual != SHA256:
            raise RuntimeError(f"Model checksum mismatch: expected {SHA256}, got {actual}")
        temporary.replace(DESTINATION)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Model verified: {DESTINATION} ({DESTINATION.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
