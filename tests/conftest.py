import pytest
from test_runtime import config as config
from test_runtime import reset_http as reset_http


@pytest.fixture(autouse=True)
def no_update_check(monkeypatch):
    """No test may reach GitHub: the update check is off unless a test re-enables it."""
    monkeypatch.setenv("ARSENKIN_DISABLE_UPDATE_CHECK", "1")
