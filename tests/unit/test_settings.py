import pytest

from aiffel.settings import Settings


def test_empty_secrets_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIFFEL_TELEGRAM_WEBHOOK_SECRET", "")
    monkeypatch.setenv("AIFFEL_MASTER_KEY", "  ")
    settings = Settings()
    assert settings.telegram_webhook_secret is None and settings.master_key is None
