import importlib

import pytest

SUBPACKAGES = [
    "igft",
    "igft.cli",
    "igft.config",
    "igft.domain",
    "igft.fetchers",
    "igft.safety",
    "igft.scanning",
    "igft.reporting",
    "igft.db",
    "igft.migrations",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name):
    importlib.import_module(name)
