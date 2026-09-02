from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bot_token: str
    openrouter_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    admin_ids: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    health_port: int = 8080

    @property
    def ai_api_key(self) -> str:
        return self.openrouter_api_key or self.openai_api_key

    @property
    def ai_base_url(self) -> str | None:
        return "https://openrouter.ai/api/v1" if self.openrouter_api_key else None

    @property
    def admins(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_ids.split(",") if x.strip().isdigit()}

@lru_cache
def get_settings() -> Settings:
    return Settings()
