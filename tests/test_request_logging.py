import json
import logging

import pytest

from config.logging import JsonFormatter

pytestmark = pytest.mark.django_db


def test_every_request_is_logged_with_method_path_status_and_duration(client, caplog):
    with caplog.at_level(logging.INFO, logger="sigtcc.request"):
        client.get("/api/health")

    entries = [
        json.loads(JsonFormatter().format(record))
        for record in caplog.records
        if record.name == "sigtcc.request"
    ]
    assert len(entries) == 1
    assert entries[0]["method"] == "GET"
    assert entries[0]["path"] == "/api/health"
    assert entries[0]["status_code"] == 200
    assert entries[0]["duration_ms"] >= 0
