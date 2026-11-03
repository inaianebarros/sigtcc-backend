import pytest

pytestmark = pytest.mark.django_db


def test_health_reports_ok_when_database_is_reachable(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
