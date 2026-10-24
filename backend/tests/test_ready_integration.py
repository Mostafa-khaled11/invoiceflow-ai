import os

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_ready_against_configured_postgresql() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    settings = Settings(database_url=database_url)

    response = TestClient(create_app(settings)).get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"
