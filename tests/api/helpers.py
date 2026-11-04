from django.contrib.auth import get_user_model

PASSWORD = "Senha-Segura-2026"


def post_json(client, url, payload):
    return client.post(url, data=payload, content_type="application/json")


def errors_by_field(response):
    return {error["loc"][-1]: error["msg"] for error in response.json()["detail"]}


def assert_rejected_on(client, url, payload, field):
    users_before = get_user_model().objects.count()

    response = post_json(client, url, payload)

    assert response.status_code == 422
    assert field in errors_by_field(response)
    assert get_user_model().objects.count() == users_before
