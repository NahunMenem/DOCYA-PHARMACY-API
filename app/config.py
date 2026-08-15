from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = ""
    core_api_url: str = ""
    jwt_secret: str = ""
    internal_api_key: str = ""
    db_pool_min_connections: int = Field(default=1, ge=1, le=5)
    db_pool_max_connections: int = Field(default=5, ge=1, le=20)
    db_pool_acquire_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    allowed_origins: str = "http://localhost:3000"
    pharmacy_token_expire_minutes: int = Field(default=720, ge=30, le=43200)
    pharmacy_payment_mode: Literal["disabled", "simulated", "mercadopago"] = "disabled"
    pharmacy_auto_activate_test_registrations: bool = False

    @field_validator("environment")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def normalized_core_api_url(self) -> str:
        return self.core_api_url.rstrip("/")

    def validate_runtime_secrets(self) -> None:
        missing = [
            name
            for name, value in (
                ("DATABASE_URL", self.database_url),
                ("CORE_API_URL", self.core_api_url),
                ("JWT_SECRET", self.jwt_secret),
                ("INTERNAL_API_KEY", self.internal_api_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        if self.db_pool_max_connections < self.db_pool_min_connections:
            raise RuntimeError("DB_POOL_MAX_CONNECTIONS must be >= DB_POOL_MIN_CONNECTIONS")
        if (
            self.pharmacy_auto_activate_test_registrations
            and self.pharmacy_payment_mode != "simulated"
        ):
            raise RuntimeError(
                "PHARMACY_AUTO_ACTIVATE_TEST_REGISTRATIONS requires simulated payment mode"
            )
        if self.environment == "production":
            # JWT_SECRET debe coincidir con el backend principal. DocYa mantiene
            # temporalmente una clave heredada de 16 caracteres para no cerrar
            # las sesiones activas; la credencial nueva entre servicios si debe
            # tener entropia suficiente.
            if len(self.jwt_secret) < 16:
                raise RuntimeError("Production JWT_SECRET must contain at least 16 characters")
            if len(self.internal_api_key) < 32:
                raise RuntimeError(
                    "Production INTERNAL_API_KEY must contain at least 32 characters"
                )


@lru_cache
def get_settings() -> Settings:
    return Settings()
