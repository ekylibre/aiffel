"""Configuration d'Aiffel, lue depuis l'environnement (préfixe ``AIFFEL_``)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIFFEL_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "production"] = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    # Domaines Ekylibre acceptés pour le raccordement (sous-domaine = tenant de la ferme).
    ekylibre_allowed_domains: tuple[str, ...] = ("ekylibre-dev.com", "ekylibre.com")
    pairing_code_ttl_minutes: int = 15

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
