from gabriel import lightning_version


def test_lightning_version():
    assert isinstance(lightning_version(), str)
    assert lightning_version()
