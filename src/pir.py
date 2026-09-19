"""Read a physical PIR connected to a Raspberry Pi GPIO pin."""


def open_pir(pin):
    from gpiozero import MotionSensor
    return MotionSensor(pin)
