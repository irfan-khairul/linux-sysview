import pytest
from unittest import mock

from sysview.__main__ import parse_args, main


def test_defaults():
    args = parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8080
    assert args.interval == 2.0


def test_overrides():
    args = parse_args(["--host", "127.0.0.1", "--port", "9000", "--interval", "5"])
    assert args.host == "127.0.0.1"
    assert args.port == 9000
    assert args.interval == 5.0


def test_rejects_invalid_port():
    with pytest.raises(SystemExit):
        parse_args(["--port", "not-a-number"])


def test_startup_failure_stops_sampler():
    """Verify that non-OSError exceptions during startup still stop the sampler."""
    with mock.patch("sysview.__main__.make_server") as mock_make_server:
        with mock.patch("sysview.__main__.Sampler") as mock_sampler_class:
            mock_sampler_instance = mock.Mock()
            mock_sampler_class.return_value = mock_sampler_instance
            mock_make_server.side_effect = TypeError("boom")

            # main() should catch the TypeError and propagate it
            with pytest.raises(TypeError, match="boom"):
                main(["--port", "8097"])

            # But the sampler should have been stopped
            mock_sampler_instance.stop.assert_called_once()
