from sysview.sampler import compute_rate


def test_rate_is_delta_over_elapsed():
    assert compute_rate(1000, 3000, 2.0) == 1000.0


def test_rate_zero_when_no_change():
    assert compute_rate(5000, 5000, 1.0) == 0.0


def test_rate_zero_when_elapsed_is_zero():
    # First sample has no previous timestamp; must not divide by zero.
    assert compute_rate(0, 5000, 0.0) == 0.0


def test_rate_zero_when_counter_resets():
    # Interface counters reset on reconnect; a negative delta is not a rate.
    assert compute_rate(9000, 100, 1.0) == 0.0
