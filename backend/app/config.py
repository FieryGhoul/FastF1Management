from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Race Data API"
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "race_data"
    frontend_origin: str = "http://localhost:5173"
    frontend_hostname: str | None = None
    fastf1_cache: Path = Path(".cache/fastf1")
    on_demand_enabled: bool = False
    on_demand_cache: Path = Path(".cache/on-demand")
    on_demand_cache_max_mb: int = 512
    admin_username: str = "admin"
    admin_password: str = "change-me"
    cookie_secure: bool = False
    session_days: int = 7
    worker_poll_seconds: float = 1.0
    scheduler_poll_seconds: float = 60.0
    historical_backfill_enabled: bool = False
    historical_backfill_start: int = 1950
    telemetry_backfill_enabled: bool = False
    # Keep shared/root defaults available, but let backend/.env override them
    # for local development when commands are run from the backend directory.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    @property
    def frontend_origins(self) -> list[str]:
        origins = [self.frontend_origin.rstrip("/")]
        if self.frontend_hostname:
            hostname = self.frontend_hostname.strip().strip("/")
            if hostname:
                origins.append(f"https://{hostname}")
        return list(dict.fromkeys(origins))


@lru_cache
def get_settings() -> Settings:
    return Settings()
