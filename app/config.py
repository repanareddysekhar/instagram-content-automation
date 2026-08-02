from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    database_path: str = "data/agent.db"
    auto_publish: bool = False
    publish_after_approval: bool = True
    mock_mode: bool = True
    admin_token: str = "change-me"
    log_level: str = "INFO"
    log_file: str = "data/agent.log"
    post_disclaimer: str = (
        "Educational content based on publicly available sources. "
        "Verify details with the original source before making decisions."
    )

    text_provider: str = "gemini"
    ai_request_timeout_seconds: float = 90.0
    ai_generation_attempts: int = 2

    openai_api_key: str = ""
    openai_text_model: str = "gpt-5.6-terra"
    openai_image_model: str = "gpt-image-2"

    gemini_api_key: str = ""
    gemini_text_model: str = "gemini-2.5-flash"
    gemini_image_model: str = "gemini-3.1-flash-image"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_image_base_url: str = "https://generativelanguage.googleapis.com/v1"

    anthropic_api_key: str = ""
    anthropic_text_model: str = "claude-haiku-4-5"
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    openai_compatible_api_key: str = ""
    # Pinning a concrete OpenAI-compatible model avoids broad router queue delays.
    openai_compatible_text_model: str = ""
    openai_compatible_base_url: str = ""

    image_provider: str = "none"
    enable_ai_art: bool = False
    reel_segment_seconds: float = 2.4
    reel_transition_seconds: float = 0.35
    reel_audio_path: str = ""
    enable_reel_voiceover: bool = True
    reel_voice: str = "Samantha"
    reel_voice_rate: int = 170

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""
    telegram_update_mode: str = "auto"
    telegram_poll_timeout_seconds: int = 25

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
    def telegram_polling_enabled(self) -> bool:
        mode = self.telegram_update_mode.lower()
        return mode == "polling" or (mode == "auto" and self.app_env == "development")

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

    @property
    def gemini_ready(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def anthropic_ready(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def openai_compatible_ready(self) -> bool:
        return bool(self.openai_compatible_base_url and self.openai_compatible_text_model)

    @property
    def text_provider_ready(self) -> bool:
        readiness = {
            "openai": self.openai_ready,
            "gemini": self.gemini_ready,
            "anthropic": self.anthropic_ready,
            "openai_compatible": self.openai_compatible_ready,
        }
        return readiness.get(self.text_provider.lower(), False)

    @property
    def image_provider_ready(self) -> bool:
        if not self.enable_ai_art:
            return False
        readiness = {
            "none": False,
            "openai": self.openai_ready,
            "gemini": self.gemini_ready,
        }
        return readiness.get(self.image_provider.lower(), False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
