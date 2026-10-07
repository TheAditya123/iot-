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
- All 23 offline tests pass, including full local event assembly, preservation
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

## Camera: working

Observed evidence:

- The Raspberry Pi Camera Module 3 Standard (SC0872/IMX708) is connected to
  CAM/DISP1 with `dtoverlay=imx708`.
- A fresh driver probe read camera module ID `0x0301`; `rpicam-hello` lists the
  4608 x 2592 sensor and its three capture modes.
- Picamera2 captured a valid 1280 x 960 JPEG of 47,833 bytes. A second real
  PIR-triggered capture was 47,580 bytes and showed an actual person.
- NanoDet counted that person with confidence `0.611` in `149.2 ms`.

Earlier probe failures were caused by the physical connection. After the module
was correctly connected on CAM/DISP1, a live reprobe succeeded; the camera did
not need replacement.

## PIR: working

Observed evidence:

- GPIO17 exists, is unclaimed, and the user has GPIO permissions.
- The GPIO pull-up/pull-down self-test passed.
- After the physical connection was corrected and the PIR warmed up, a real
  motion test produced a rising edge on BCM GPIO17.

The HC-SR501 trigger path is therefore ready for the full application.

## Completion gate

A real PIR edge produced event `c00c680d-d991-4ed1-998d-7a8ac04f9e0e`, a real
JPEG, `people_count: 1`, confidence `0.611`, `149.2 ms` inference, and an atomic
TinyDB row. AWS IoT acknowledged that same stored event on `iot/room/events`,
and the dashboard reads it from TinyDB. A second production event correctly
reported `people_count: 0` for an empty camera view, added the one-sentence
summary, and was also acknowledged by AWS. The complete physical MVP path
passes.
