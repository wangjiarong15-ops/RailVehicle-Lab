"""Give AppTest pages a disposable local data directory before test imports."""

from tempfile import TemporaryDirectory

import pytest


def pytest_configure(config):
    test_cache = config.rootpath / ".pytest_cache"
    test_cache.mkdir(exist_ok=True)
    test_home = TemporaryDirectory(prefix="rail-test-home-", dir=test_cache)
    environment = pytest.MonkeyPatch()
    environment.setenv("RAIL_PORTABLE_HOME", test_home.name)
    config.add_cleanup(test_home.cleanup)
    config.add_cleanup(environment.undo)
