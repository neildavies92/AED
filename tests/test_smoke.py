import importlib


def test_package_is_importable() -> None:
    package = importlib.import_module("ask_every_day")
    assert package.__name__ == "ask_every_day"
