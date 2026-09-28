from gabriel import version


def test_version():
    assert isinstance(version(), str)
    assert version()
