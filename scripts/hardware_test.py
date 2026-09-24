"""Run one focused test against real Pi hardware; never simulates a device."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.camera import make_camera, save_jpeg_atomic
from src.config import Config
from src.pir import open_pir


def test_camera(config, output):
    if config.camera_backend == "pi":
        try:
            probe = subprocess.run(
                ["rpicam-hello", "--list-cameras"], capture_output=True,
                text=True, timeout=15, check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "rpicam-hello is missing; install the Raspberry Pi camera apps"
            ) from exc
        probe_output = (probe.stdout + probe.stderr).strip()
        if probe.returncode != 0 or "No cameras available" in probe_output:
            raise RuntimeError(
                "Raspberry Pi OS found zero CSI camera sensors. The camera stack is installed, "
                "but no sensor answered during kernel/libcamera probing; power off and check the "
                "ribbon orientation, both latches, the Pi 5 22-pin cable, and the other CAM/DISP port"
            )
    camera = make_camera(config)
    if camera is None:
        raise RuntimeError("CAMERA_BACKEND is off")
    try:
        image = camera.capture()
        save_jpeg_atomic(image, output)
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
        high_samples = int(was_active)
        low_samples = int(not was_active)
        transitions = 0
        while time.monotonic() < deadline:
            active = pir.motion_detected
            high_samples += int(active)
            low_samples += int(not active)
            transitions += int(active != was_active)
            saw_inactive = saw_inactive or not active
            if saw_inactive and active and not was_active:
                print(
                    f"PASS: real PIR rising edge detected on GPIO{config.gpio} "
                    f"({transitions} transition(s) observed)"
                )
                return
            was_active = active
            time.sleep(0.05)
        if high_samples == 0:
            detail = (
                "GPIO stayed LOW for the entire test: no HIGH signal reached the Pi. "
                "Verify HC-SR501 VCC to physical pin 2, GND to pin 6, OUT to physical "
                "pin 11 (BCM17), then increase sensitivity and walk across its view"
            )
        elif low_samples == 0:
            detail = (
                "GPIO stayed HIGH for the entire test: let the PIR settle, reduce its "
                "delay, select non-retrigger mode, and move out of its view before retrying"
            )
        else:
            detail = (
                f"GPIO changed {transitions} time(s), but no inactive-to-active "
                "edge was accepted"
            )
        raise TimeoutError(f"No real PIR rising edge within {timeout:.0f} seconds. {detail}")
    finally:
        pir.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("camera", "pir"))
    parser.add_argument("--env", help="Optional environment file")
    parser.add_argument("--output", type=Path, default=Path("/tmp/iot-camera-test.jpg"))
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        config = Config.load(args.env, local_only=True)
        if args.component == "camera":
            test_camera(config, args.output)
        else:
            test_pir(config, args.timeout)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        parser.exit(1, f"Hardware test failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Hardware test stopped.\n")


if __name__ == "__main__":
    main()
