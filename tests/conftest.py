import os

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure config-related env vars don't leak between tests."""
    for key in list(os.environ):
        if key.startswith(("R2_", "BACKUP_", "KEEP_", "RUN_ONCE", "INTERVAL_", "DRY_RUN", "LOG_LEVEL")):
            monkeypatch.delenv(key, raising=False)
