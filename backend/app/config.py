from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DRONE_POC_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'backend' / 'data' / 'poc.db'}"
    legacy_json: Path = PROJECT_ROOT / "backend" / "data" / "poc.json"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    prototype_root: Path = PROJECT_ROOT / "无人机设计方案"
