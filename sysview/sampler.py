"""Background sampling of counter-based system metrics."""


def compute_rate(prev_value, cur_value, elapsed):
    """Return per-second rate between two counter readings.

    Returns 0.0 when elapsed time is non-positive or the counter decreased
    (which happens when an interface resets), since neither yields a
    meaningful rate.
    """
    if elapsed <= 0:
        return 0.0
    delta = cur_value - prev_value
    if delta < 0:
        return 0.0
    return delta / elapsed
