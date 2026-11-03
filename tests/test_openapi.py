def test_openapi_documentation_page_loads(client):
    response = client.get("/api/docs")

    assert response.status_code == 200


def test_openapi_schema_describes_the_health_endpoint(client):
    response = client.get("/api/openapi.json")

    assert response.status_code == 200
    assert "/api/health" in response.json()["paths"]
