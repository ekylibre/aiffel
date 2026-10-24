import json

import structlog
from fastapi.testclient import TestClient

from aiffel.app.main import create_app
from aiffel.logging import redact
from aiffel.settings import Settings


def test_healthz() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz() -> None:
    client = TestClient(create_app())
    assert client.get("/readyz").status_code == 200


def test_openapi_docs_are_disabled() -> None:
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 404


def test_secrets_are_not_shown_in_repr() -> None:
    settings = Settings(telegram_bot_token="123:abc", master_key="tres-secret")
    assert "123:abc" not in repr(settings)
    assert "tres-secret" not in repr(settings)


def test_sensitive_log_fields_are_redacted() -> None:
    event = redact(
        None, "info", {"event": "tg.message_received", "text": "ma parcelle", "token": "x"}
    )
    rendered = json.dumps(dict(event), ensure_ascii=False)
    assert "ma parcelle" not in rendered
    assert '"token": "[masqué]"' in rendered


def test_logging_pipeline_redacts(capsys) -> None:
    from aiffel.logging import configure_logging

    configure_logging("INFO")
    structlog.get_logger().info("connect", password="hunter2", farm="demo")
    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert "demo" in out
