from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Race Data API"
    database_url: str = "sqlite:///./race_data.db"
    frontend_origin: str = "http://localhost:5173"
    fastf1_cache: Path = Path(".cache/fastf1")
    admin_username: str = "admin"
    admin_password: str = "change-me"
    cookie_secure: bool = False
    session_days: int = 7
    worker_poll_seconds: float = 1.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

