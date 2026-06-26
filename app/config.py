from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    backend_mode: str = "mock"
    compatibility_http_200: bool = True

    databricks_host: str = ""
    databricks_token: str = ""
    databricks_endpoint_name: str = ""
    databricks_timeout_seconds: float = 30.0

    retry_after_seconds: int = 60
    mock_sleep_seconds: float = 0.0

    # Stateful scale-to-zero simulation for the mock:cold_start route.
    mock_warmup_seconds: float = 10.0
    mock_idle_reset_seconds: float = 30.0


settings = Settings()
