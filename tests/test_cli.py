import pytest

from sysview.__main__ import parse_args


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
