import time


class SimulatedPIR:
    def __init__(self, interval):
        self.interval = interval
        self.next_event = time.monotonic() + interval

    def triggered(self):
        if time.monotonic() < self.next_event:
            return False
        self.next_event = time.monotonic() + self.interval
        return True

    def close(self):
        pass


class GPIOPIR:
    def __init__(self, pin):
        from gpiozero import MotionSensor
        self.sensor = MotionSensor(pin)
        self.was_active = False

    def triggered(self):
        active = self.sensor.motion_detected
        rising = active and not self.was_active
        self.was_active = active
        return rising

    def close(self):
        self.sensor.close()


def make_pir(config):
    return SimulatedPIR(config.interval) if config.pir_backend == "simulated" else GPIOPIR(config.gpio)
