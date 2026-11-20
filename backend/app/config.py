from functools import lru_cache
import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Disk responses contain signed upload/download URLs; do not retain them in INFO logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "sqlite:///./chest.db"
    bot_token: str = ""
    telegram_proxy: str = ""
    allowed_telegram_ids: str = ""
    public_url: str = "http://localhost:8000"
    init_data_ttl: int = 86400
    dev_auth: bool = False
    dev_user_id: int = 1
    file_encryption_key: str = ""
    data_dir: Path = Path("data")
    storage_backend: str = "local"
    yandex_disk_token: str = ""
    yandex_disk_root: str = "app:/TG.Chest"
    openrouter_api_key: str = ""
    openrouter_proxy: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536
    llm_model: str = "qwen/qwen3-30b-a3b-instruct-2507"
    rerank_model: str = ""
    trusted_ai_base_url: str = ""
    trusted_ai_api_key: str = ""
    trusted_ai_model: str = ""
    trusted_ai_proxy: str = ""
    trusted_embedding_model: str = ""
    max_file_mb: int = 20
    backups_enabled: bool = False
    backup_retention_days: int = 7

    @property
    def allowed_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.allowed_telegram_ids.split(",") if x.strip()}

    def validate_runtime(self):
        if self.environment == "production":
            if self.dev_auth or not self.bot_token or not self.allowed_ids:
                raise RuntimeError("Production requires BOT_TOKEN, ALLOWED_TELEGRAM_IDS and DEV_AUTH=false")
            if not self.public_url.startswith("https://"):
                raise RuntimeError("Production PUBLIC_URL must use HTTPS")
            if not self.file_encryption_key:
                raise RuntimeError("FILE_ENCRYPTION_KEY is required")
        if self.storage_backend not in {"local", "yandex"}:
            raise RuntimeError("STORAGE_BACKEND must be local or yandex")
        if self.storage_backend == "yandex" and not self.yandex_disk_token:
            raise RuntimeError("YANDEX_DISK_TOKEN is required")


@lru_cache
def get_settings() -> Settings:
    return Settings()
