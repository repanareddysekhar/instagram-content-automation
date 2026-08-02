from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    database_path: str = "data/agent.db"
    auto_publish: bool = False
    mock_mode: bool = True
    admin_token: str = "change-me"

    openai_api_key: str = ""
    openai_text_model: str = "gpt-5.6-terra"
    openai_image_model: str = "gpt-image-2"
    enable_ai_art: bool = False

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""

    meta_graph_base_url: str = "https://graph.instagram.com"
    meta_graph_api_version: str = ""
    instagram_user_id: str = ""
    instagram_access_token: str = ""
    instagram_insight_metrics: str = (
        "views,reach,likes,comments,saves,shares,total_interactions"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_file(self) -> Path:
        return Path(self.database_path)

    @property
    def generated_dir(self) -> Path:
        return Path("data/generated")

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def instagram_ready(self) -> bool:
        return bool(
            self.meta_graph_api_version
            and self.instagram_user_id
            and self.instagram_access_token
        )

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()

