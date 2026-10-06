import sandman_cli


def test_version() -> None:
    assert sandman_cli.__version__ == "0.1.0"
