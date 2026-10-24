"""Configuration d'Aiffel, lue depuis l'environnement (préfixe ``AIFFEL_``)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIFFEL_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "production"] = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    # Domaines Ekylibre acceptés pour le raccordement (sous-domaine = tenant de la ferme).
    ekylibre_allowed_domains: tuple[str, ...] = ("ekylibre-dev.com", "ekylibre.com")
    pairing_code_ttl_minutes: int = 15

    # Moteur d'agent (A4). Modèle et effort modifiables sans toucher au code (NF-SOB).
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_max_tokens: int = 16000
    agent_max_iterations: int = 8
    lexicon_mcp_url: str = "https://lexicon.osfarm.org/mcp"

    # Vocal (A6) : faster-whisper local, configuration reprise de Duke.
    stt_enabled: bool = True
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str = "fr"
    whisper_cache_dir: str | None = None
    voice_max_seconds: int = 180

    # Secrets : SecretStr empêche leur affichage dans les logs et les repr.
    # Connexion applicative (rôle aiffel_app, soumis à la RLS).
    database_url: SecretStr | None = None
    # Connexion des migrations (propriétaire du schéma) ; par défaut la même que database_url.
    migration_database_url: SecretStr | None = None
    master_key: SecretStr | None = None
    hash_salt: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    @field_validator(
        "database_url",
        "migration_database_url",
        "master_key",
        "hash_salt",
        "telegram_bot_token",
        "telegram_webhook_secret",
        "anthropic_api_key",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # « AIFFEL_X= » dans .env vaut « non défini » : un secret vide activerait sinon le
        # webhook Telegram en acceptant les requêtes sans en-tête secret.
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
