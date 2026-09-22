"""Run one focused test against real Pi hardware; never simulates a device."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.camera import make_camera
from src.config import Config
from src.environmental import make_environment
from src.pir import open_pir


def test_camera(config, output):
    camera = make_camera(config)
    if camera is None:
        raise RuntimeError("CAMERA_BACKEND is off")
    try:
        image = camera.capture()
        image.save(output, "JPEG")
    finally:
        camera.close()
    if output.stat().st_size == 0:
        raise RuntimeError("Camera created an empty file")
    print(f"PASS: real camera JPEG saved to {output} ({output.stat().st_size} bytes)")


def test_pir(config, timeout):
    pir = open_pir(config.gpio)
    try:
        print(f"Warming physical PIR on GPIO{config.gpio} for {config.warmup:.0f} seconds...")
        time.sleep(config.warmup)
        print("Waiting for the PIR to become inactive and then detect a rising edge...")
        deadline, was_active = time.monotonic() + timeout, pir.motion_detected
        saw_inactive = not was_active
        while time.monotonic() < deadline:
            active = pir.motion_detected
            saw_inactive = saw_inactive or not active
            if saw_inactive and active and not was_active:
                print(f"PASS: real PIR rising edge detected on GPIO{config.gpio}")
                return
            was_active = active
            time.sleep(0.05)
        raise TimeoutError(f"No real PIR rising edge within {timeout:.0f} seconds")
    finally:
        pir.close()


def test_environment(config):
    sensor = make_environment(config)
    if sensor is None:
        raise RuntimeError("ENV_SENSOR_ENABLED is false")
    try:
        reading = sensor.read()
    finally:
        sensor.close()
    print(f"PASS: real BME280 at 0x{sensor.address:02x}: {reading}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("camera", "pir", "environment"))
    parser.add_argument("--env", help="Optional environment file")
    parser.add_argument("--output", type=Path, default=Path("/tmp/iot-camera-test.jpg"))
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        config = Config.load(args.env, local_only=True)
        if args.component == "camera":
            test_camera(config, args.output)
        elif args.component == "pir":
            test_pir(config, args.timeout)
        else:
            test_environment(config)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        parser.exit(1, f"Hardware test failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Hardware test stopped.\n")


if __name__ == "__main__":
    main()
