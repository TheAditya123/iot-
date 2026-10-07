# Raspberry Pi 5 bring-up status — 2026-10-07

This report records observed results from the real target Pi. It distinguishes
verified software from hardware paths that still require a physical correction.
No simulated sensor values were inserted into the production database.

## Verified on the Pi

- Raspberry Pi OS is running kernel `6.18.50+rpt-rpi-2712` on ARM64.
- The interrupted OS package transaction was allowed to finish and the Pi was
  rebooted cleanly; Chromium `153.0.8010.52` now launches normally.
- Wi-Fi passed DNS, HTTPS, five ICMP checks with zero loss, and the MQTT soak.
  NetworkManager is configured to disable Wi-Fi power saving on the next
  reconnect; the live connection was deliberately left up during this work.
- SSH is enabled and active, mDNS resolves `raspberrypi.local`, and the dashboard
  returns HTTP 200 through `http://raspberrypi.local:5000/` on the LAN.
- NTP reports synchronized and the clock was within one second of an HTTPS
  server timestamp, which is required for reliable TLS certificate checks.
- Before sustained inference, the Pi reported no undervoltage or thermal
  throttling (`get_throttled=0x0`).
- The Python environment imports GPIO Zero, lgpio, Picamera2, TinyDB,
  OpenCV, Flask, Paho MQTT, boto3, and AWS CRT.
- All 21 offline tests pass, including full local event assembly, preservation
  of partial hardware-failure events, MQTT acknowledgments,
  exact-topic IoT policy generation, atomic TinyDB/JPEG/private-file recovery,
  dashboard data, and persistent outbox retry.
- The pinned 3.8 MB OpenCV Zoo NanoDet model matches SHA-256
  `4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186`.
- The detector counted two people in OpenCV's basketball image with confidence
  values `0.831` and `0.696`, and returned zero people for the fruit image.
- A 100-inference CPU stress run counted those same two people in all 100 runs:
  mean latency was 116.1 ms (90.1–133.6 ms), CPU temperature rose from 68.1°C
  to 77.4°C, and the Pi still reported no throttling. This is direct motivation
  for triggering inference from the low-cost PIR instead of running it forever.
- A second 100-inference run on the real no-person image returned zero people
  in all 100 runs (118.2 ms mean) and also caused no throttling.
- A longer continuous run alternated the two real images for 500 inferences;
  all 250 positive and 250 negative results were correct, mean latency was
  119.7 ms, and resident memory stayed near 82 MB after initial model buffers
  were allocated. Continuous CPU inference raised the Pi to 82.3°C and activated
  its soft thermal limit (`get_throttled=0xe0008`). This is concrete evidence
  that PIR-triggered inference avoids sustained heat and CPU pressure. After
  cooling, the live limit cleared and the ARM clock returned to 2.4 GHz. A
  three-minute Wi-Fi/dashboard health check passed while temperature returned
  to 68.1°C; only the expected historical flags remained (`0xe0000`).
- AWS IoT Thing `room-monitor-pi`, policy `CameraPirMqttPolicy`, active X.509
  certificate, and Data-ATS endpoint are provisioned in `us-east-1`.
- The device certificate and private key are a matching pair, the key is mode
  `0600`, and the certificate is valid through 2049.
- TLS/QoS 1 publish-and-return tests pass on `iot/setup/test` and
  `iot/room/events`.
- A 5.5-minute soak sent 12 messages at 30-second intervals through the
  production publisher; AWS acknowledged all 12 with QoS 1 and no drop.
- The production TinyDB outbox and `MQTTPublisher` published a temporary
  `outbox_connection_test`, received the AWS acknowledgment, and marked the
  temporary row published. The real sensor database remained empty.
- With an intentionally offline endpoint, the production outbox retained the
  row across process restart instead of reporting a false publish.
- Atomic storage survived a stress run of 500 writes and 800 concurrent
  dashboard-style reads with all 500 unique events retained.
- Captured JPEGs are also written to a temporary file, synced, and atomically
  installed; an interrupted-save test preserved the prior valid image.
- The dashboard serves HTTP 200 on `0.0.0.0:5000`; `/api/status` returns `null`
  and `/api/events` returns `[]` until the first real event exists.
- A five-minute dashboard soak served all 180 HTML/status/events requests
  successfully while preserving the empty production database.
- Temporary AWS administrator login credentials were logged out after device
  provisioning. MQTT continues to work with the scoped device certificate.

## Camera: why it is not working yet

Observed evidence:

- The module is a Raspberry Pi Camera Module 3 Standard, part SC0872, using the
  Sony IMX708 sensor.
- Both CAM/DISP connectors and the standard sensor overlays were tested. The
  current setup uses CAM/DISP1 with the correct `dtoverlay=imx708` setting.
- The kernel creates the CAM/DISP1 IMX708 path and loads the correct driver, so
  the selected port, overlay, and Linux camera support are confirmed.
- The IMX708 at address `0x1a` fails its chip-ID read with error `-5`.
- The module's DW9807 autofocus controller at address `0x0c` fails I2C writes
  with error `-121`.
- `rpicam-hello --list-cameras` therefore still reports `No cameras available!`.
- A production `src.main --local-only --count 1` run exited nonzero at camera
  initialization and left both `data/events.json` and `images/` empty; it did
  not manufacture a capture or event.

This rules out Python, Picamera2, libcamera, the selected port, and the sensor
model setting. Both chips on the same camera board fail to communicate, so the
remaining shared failure point is the 15-to-22-pin ribbon cable or the camera
board. The next check is a known-good Raspberry Pi Standard-to-Mini camera
cable. If the same errors remain with that cable, replace/test the SC0872 board.

## PIR: working

Observed evidence:

- GPIO17 exists, is unclaimed, and the user has GPIO permissions.
- The GPIO pull-up/pull-down self-test passed.
- After the physical connection was corrected and the PIR warmed up, a real
  motion test produced a rising edge on BCM GPIO17.

The HC-SR501 trigger path is therefore ready for the full application.

## Completion gate

After camera communication is restored, one real PIR edge must produce a real
JPEG, a measured person count and latency, an atomic TinyDB row, an AWS
acknowledgment, and visible dashboard data. The PIR, software, AWS connection,
and dashboard are ready for that sequence and do not fall back to fake values.
