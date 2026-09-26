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
    weather_base_url: str = "https://archive-api.open-meteo.com/v1/archive"
    weather_timeout_seconds: float = 3.0
    weather_retry_count: int = 1
    weather_cache_ttl_seconds: int = 3600
    demo_latitude: float = 31.9539
    demo_longitude: float = 35.9106
    llm_provider: str = "disabled"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = ""
    llm_timeout_seconds: float = 10.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
