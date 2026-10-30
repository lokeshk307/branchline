from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./branchline.db"
    secret_key: str = "dev-secret-change-me"
    cors_origins: str = "http://localhost:5173"
    sync_jobs: bool = True
    redis_url: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_webhook_secret: str = ""
    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key: str = ""
    public_api_url: str = "http://localhost:8000"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    sentry_dsn: str = ""
    allow_simulate: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
