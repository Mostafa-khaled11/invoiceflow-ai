from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_health_returns_expected_payload() -> None:
    client = TestClient(create_app(Settings(database_url=None)))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "invoiceflow-api",
    }


def test_ready_returns_service_unavailable_without_database() -> None:
    client = TestClient(create_app(Settings(database_url=None)))

    response = client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "PostgreSQL is not ready"}
