"""Shared test fixtures.

Environment must be set before ``app.config`` is imported, since Paths reads it
at import time — hence the module-level assignment here (conftest is imported
before the test modules).
"""

import os
import shutil
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="tmm-test-")
os.environ["TMM_DATA_DIR"] = _TMP
os.environ["TMM_DOWNLOADS_DIR"] = os.path.join(_TMP, "downloads")
os.environ["TMM_MIHOMO_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
