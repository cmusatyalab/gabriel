from gabriel import add, version


def test_version():
    assert isinstance(version(), str)
    assert version()


def test_add():
    assert add(2, 3) == 5
