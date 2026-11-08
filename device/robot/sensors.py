"""Distance reading: one reading per control step. Never block the loop."""


def read_distance(sensor):
    """Return a distance in cm, or None when the sensor gave no usable echo."""
    return sensor.read_cm()
