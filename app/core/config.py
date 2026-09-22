from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MeterDetective API"
    app_env: str = "development"
    app_debug: bool = False
    database_url: str = (
        "postgresql+psycopg://meterdetective:meterdetective@localhost:5432/meterdetective"
    )
    database_connect_timeout_seconds: int = 3

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
