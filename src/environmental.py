"""Read compensated values from a physical Bosch BME280 over I2C."""
from __future__ import annotations

import logging
import time

LOG = logging.getLogger(__name__)


def _signed(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


class BME280:
    """Minimal BME280 driver using the calibration stored in the sensor."""

    CHIP_ID = 0x60

    def __init__(self, bus_number: int = 1, address: int | None = None):
        try:
            from smbus2 import SMBus
            self.bus = SMBus(bus_number)
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                f"Cannot open I2C bus {bus_number}; enable I2C and check /dev/i2c-{bus_number}"
            ) from exc

        try:
            self.address = address if address is not None else self._detect()
            chip_id = self.bus.read_byte_data(self.address, 0xD0)
            if chip_id != self.CHIP_ID:
                raise RuntimeError(
                    f"I2C device at 0x{self.address:02x} is not a BME280 (chip ID 0x{chip_id:02x})"
                )
            self.calibration = self._read_calibration()
            self.bus.write_byte_data(self.address, 0xF2, 0x01)
            self.bus.write_byte_data(self.address, 0xF5, 0x00)
        except BaseException:
            self.bus.close()
            raise

    def _detect(self) -> int:
        for address in (0x76, 0x77):
            try:
                if self.bus.read_byte_data(address, 0xD0) == self.CHIP_ID:
                    return address
            except OSError:
                continue
        raise RuntimeError("No BME280 detected at I2C address 0x76 or 0x77")

    def _u16(self, register: int) -> int:
        low, high = self.bus.read_i2c_block_data(self.address, register, 2)
        return low | high << 8

    def _s16(self, register: int) -> int:
        return _signed(self._u16(register), 16)

    def _read_calibration(self) -> dict[str, int]:
        e4 = self.bus.read_byte_data(self.address, 0xE4)
        e5 = self.bus.read_byte_data(self.address, 0xE5)
        e6 = self.bus.read_byte_data(self.address, 0xE6)
        return {
            "T1": self._u16(0x88), "T2": self._s16(0x8A), "T3": self._s16(0x8C),
            "P1": self._u16(0x8E), "P2": self._s16(0x90), "P3": self._s16(0x92),
            "P4": self._s16(0x94), "P5": self._s16(0x96), "P6": self._s16(0x98),
            "P7": self._s16(0x9A), "P8": self._s16(0x9C), "P9": self._s16(0x9E),
            "H1": self.bus.read_byte_data(self.address, 0xA1),
            "H2": self._s16(0xE1), "H3": self.bus.read_byte_data(self.address, 0xE3),
            "H4": _signed((e4 << 4) | (e5 & 0x0F), 12),
            "H5": _signed((e6 << 4) | (e5 >> 4), 12),
            "H6": _signed(self.bus.read_byte_data(self.address, 0xE7), 8),
        }

    def read(self) -> dict[str, float]:
        # Forced mode performs one fresh, low-power measurement per PIR event.
        self.bus.write_byte_data(self.address, 0xF2, 0x01)
        self.bus.write_byte_data(self.address, 0xF4, 0x25)
        time.sleep(0.01)
        deadline = time.monotonic() + 0.2
        while self.bus.read_byte_data(self.address, 0xF3) & 0x08:
            if time.monotonic() >= deadline:
                raise TimeoutError("BME280 measurement did not complete")
            time.sleep(0.005)
        data = self.bus.read_i2c_block_data(self.address, 0xF7, 8)
        adc_p = data[0] << 12 | data[1] << 4 | data[2] >> 4
        adc_t = data[3] << 12 | data[4] << 4 | data[5] >> 4
        adc_h = data[6] << 8 | data[7]
        if adc_t == 0x80000 or adc_p == 0x80000:
            raise RuntimeError("BME280 returned a disabled measurement channel")

        c = self.calibration
        var1 = (adc_t / 16384.0 - c["T1"] / 1024.0) * c["T2"]
        var2 = ((adc_t / 131072.0 - c["T1"] / 8192.0) ** 2) * c["T3"]
        t_fine = var1 + var2
        temperature = t_fine / 5120.0

        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * c["P6"] / 32768.0
        var2 += var1 * c["P5"] * 2.0
        var2 = var2 / 4.0 + c["P4"] * 65536.0
        var1 = (c["P3"] * var1 * var1 / 524288.0 + c["P2"] * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * c["P1"]
        if var1 == 0:
            raise RuntimeError("BME280 pressure calibration is invalid")
        pressure = 1048576.0 - adc_p
        pressure = (pressure - var2 / 4096.0) * 6250.0 / var1
        pressure += (c["P9"] * pressure * pressure / 2147483648.0
                     + pressure * c["P8"] / 32768.0 + c["P7"]) / 16.0

        humidity = t_fine - 76800.0
        humidity = (adc_h - (c["H4"] * 64.0 + c["H5"] / 16384.0 * humidity)) * (
            c["H2"] / 65536.0
            * (1.0 + c["H6"] / 67108864.0 * humidity
               * (1.0 + c["H3"] / 67108864.0 * humidity))
        )
        humidity *= 1.0 - c["H1"] * humidity / 524288.0

        return {
            "temperature_c": round(temperature, 2),
            "humidity_pct": round(max(0.0, min(100.0, humidity)), 2),
            "pressure_hpa": round(pressure / 100.0, 2),
        }

    def close(self) -> None:
        self.bus.close()


def make_environment(config):
    if not config.env_sensor_enabled:
        return None
    sensor = BME280(config.i2c_bus, config.bme280_address)
    LOG.info("BME280 detected at 0x%02x on I2C bus %d", sensor.address, config.i2c_bus)
    return sensor
