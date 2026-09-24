# Raspberry Pi 5 bring-up status — 2026-09-23

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
- The Python environment imports GPIO Zero, lgpio, Picamera2, smbus2, TinyDB,
  OpenCV, Flask, Paho MQTT, boto3, and AWS CRT.
- All 22 offline tests pass, including full local event assembly, preservation
  of partial hardware-failure events, BME280 compensation, MQTT acknowledgments,
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

- `rpicam-hello --list-cameras` reports `No cameras available!`.
- `rpicam-still` reaches libcamera normally, then reports zero cameras.
- The kernel log contains the Pi ISP but no `imx*` or `ov*` sensor probe.
- No sensor capture node exists; `/dev/video19` is the HEVC decoder and
  `/dev/video20` through `/dev/video35` are ISP processing nodes.
- `camera_auto_detect=1` is present, but no camera device-tree overlay loaded.
- Both camera regulator GPIOs were observed low, consistent with no sensor being
  identified and powered by the camera subsystem.
- No USB webcam is attached as an alternate real camera.
- A production `src.main --local-only --count 1` run exited nonzero at camera
  initialization and left both `data/events.json` and `images/` empty; it did
  not manufacture a capture or event.

This rules out Python, Picamera2 installation, and missing camera applications.
For an official Raspberry Pi OV5647, IMX219, IMX708, IMX477, IMX500, or IMX296
module, Raspberry Pi OS auto-detection should load the driver. The evidence
therefore points to an open/reversed ribbon, an unlocked connector, the wrong
15-to-22-pin cable, a damaged module/cable, or a third-party sensor that needs
its documented manual overlay. Identify the sensor before adding an overlay.

Required physical correction: power the Pi off, reseat both ribbon ends with
their contacts facing the connector contacts, close both latches, verify the Pi
5 22-pin end, and try the other CAM/DISP connector. Then rerun
`rpicam-hello --list-cameras` and capture a nonempty JPEG.

## PIR: why it is not working yet

Observed evidence:

- GPIO17 exists, is unclaimed, and the user has GPIO permissions.
- After the sensor had several minutes to stabilize, GPIO17 stayed LOW for every
  sample during separate two-minute and five-minute real transition tests.
- A safe internal-pull self-test made GPIO17 read HIGH with pull-up and LOW with
  pull-down.

The self-test proves the Pi input and GPIO software work. The motion test proves
that no HIGH signal reaches physical pin 11. Because the weak pull-up could pull
the line HIGH, the input also appears to be floating rather than held LOW by an
actively connected HC-SR501 output. The likely causes are missing sensor power,
OUT on the wrong header pin, ground not shared, a loose jumper, or a faulty PIR.

Required physical correction: verify the labels on the module itself, then wire
VCC to physical pin 2, GND to physical pin 6, and OUT to physical pin 11. Raise
sensitivity, allow warmup, and walk across the sensor view while rerunning
`scripts/hardware_test.py pir`.

## BME280: why it is not working yet

Observed evidence:

- `/dev/i2c-1` does not exist.
- `raspi-config nonint get_i2c` returns `1`, meaning header I2C is disabled.
- Only internal buses 13 and 14 exist; these are not the GPIO2/GPIO3 header bus.
- The Python BME280 driver correctly reports that `/dev/i2c-1` does not exist
  and that header I2C must be enabled before rebooting.
- Enabling the sensor in the production pipeline exits nonzero with the exact
  missing-device path and I2C-enable command; it does not invent measurements.

The OS cannot communicate with any header I2C sensor until bus 1 is enabled.
This is independent of the BME280 library and does not prove a BME280 is wired.

Required privileged/physical correction: run
`sudo raspi-config nonint do_i2c 0`, power off, wire BME280 VCC to 3.3 V, GND to
ground, SDA to physical pin 3, and SCL to physical pin 5, then boot and check
`i2cdetect -y 1` for `76` or `77`.

## Completion gate

After the three physical paths are corrected, one real PIR edge must produce a
real JPEG, a measured person count and latency, optional real BME280 readings,
an atomic TinyDB row, an AWS acknowledgment, and visible dashboard data. The
software is configured for that sequence and does not fall back to fake values.
