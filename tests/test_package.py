import sysview


def test_package_exposes_version():
    assert isinstance(sysview.__version__, str)
    assert sysview.__version__
