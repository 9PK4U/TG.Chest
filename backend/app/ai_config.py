"""Live provider configuration. Secret values are encrypted and never returned to UI."""
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .db import SessionLocal
from .models import AIConfiguration
from .storage import cipher

Policy = Literal["external", "trusted"]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    base_url: str = Field(default="", max_length=2000)
    api_key: str = Field(default="", max_length=4096)
    proxy_url: str = Field(default="", max_length=2000)
    bot_proxy_url: str = Field(default="", max_length=2000)
    chat_model: str = Field(default="", max_length=200)
    organize_model: str = Field(default="", max_length=200)
    embedding_model: str = Field(default="", max_length=200)
    rerank_model: str = Field(default="", max_length=200)
    temperature: float = Field(default=0.1, ge=0, le=2)
    answer_max_tokens: int = Field(default=1200, ge=128, le=32000)
    organize_max_tokens: int = Field(default=12000, ge=256, le=64000)
    timeout_seconds: int = Field(default=120, ge=5, le=300)
    top_k: int = Field(default=8, ge=1, le=20)
    send_embedding_dimensions: bool = True

    @property
    def connection_ready(self):
        return bool(self.enabled and self.base_url and (self.api_key or urlsplit(self.base_url).hostname != "openrouter.ai"))

    @field_validator("base_url", "proxy_url", "bot_proxy_url")
    @classmethod
    def valid_url(cls, value, info):
        value = value.strip().rstrip("/")
        if not value:
            return value
        url = urlsplit(value)
        schemes = {"http", "https"} if info.field_name == "base_url" else {"http", "https", "socks5"}
        if url.scheme not in schemes or not url.hostname or url.query or url.fragment or url.username or url.password:
            raise ValueError("Use an HTTP(S) API URL or HTTP(S)/SOCKS5 proxy URL without embedded credentials")
        return value

    @field_validator("chat_model", "organize_model", "embedding_model", "rerank_model")
    @classmethod
    def model_name(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Invalid model name")
        return value.strip()


class ProviderUpdate(ProviderConfig):
    api_key: str | None = Field(default=None, max_length=4096)
    revision: int = Field(ge=0)
    clear_api_key: bool = False


class ProviderTest(BaseModel):
    task: Literal["chat", "organize", "embeddings", "rerank", "models"] = "chat"
    draft: ProviderUpdate | None = None


def defaults(policy: Policy) -> ProviderConfig:
    s = get_settings()
    if policy == "external":
        return ProviderConfig(base_url=s.openrouter_base_url, api_key=s.openrouter_api_key,
            proxy_url=s.openrouter_proxy, bot_proxy_url=s.telegram_proxy,
            chat_model=s.llm_model, embedding_model=s.embedding_model, rerank_model=s.rerank_model)
    return ProviderConfig(base_url=s.trusted_ai_base_url, api_key=s.trusted_ai_api_key,
        proxy_url=s.trusted_ai_proxy, bot_proxy_url=s.trusted_ai_proxy,
        chat_model=s.trusted_ai_model, embedding_model=s.trusted_embedding_model)


def decode(record):
    return ProviderConfig.model_validate_json(cipher().decrypt(record.payload.encode()))


def effective(policy: Policy, db=None):
    if db is None:
        with SessionLocal() as connection:
            return effective(policy, connection)
    record = db.get(AIConfiguration, policy)
    # Missing/invalid DB state must not silently enable a different external provider.
    return (decode(record), record.revision) if record else (defaults(policy), 0)


def public_config(config, revision):
    result = config.model_dump(exclude={"api_key"})
    result.update(api_key_set=bool(config.api_key), revision=revision,
                  configured=bool(config.connection_ready and (config.chat_model or config.organize_model)),
                  embedding_dimensions=get_settings().embedding_dimensions)
    return result


def merge_update(current, request):
    values = request.model_dump(exclude={"revision", "clear_api_key", "api_key"})
    values["api_key"] = "" if request.clear_api_key else request.api_key if request.api_key is not None else current.api_key
    return ProviderConfig.model_validate(values)


def save(policy: Policy, request: ProviderUpdate, db):
    current, revision = effective(policy, db)
    if request.revision != revision:
        raise HTTPException(409, "Настройки уже изменились. Перезагрузите их перед сохранением.")
    config = merge_update(current, request)
    encrypted = cipher().encrypt(config.model_dump_json().encode()).decode()
    try:
        if revision:
            result = db.execute(update(AIConfiguration).where(AIConfiguration.id == policy,
                AIConfiguration.revision == revision).values(payload=encrypted, revision=revision + 1, updated_at=time.time()))
            if not result.rowcount:
                raise HTTPException(409, "Настройки уже изменились")
        else:
            db.add(AIConfiguration(id=policy, payload=encrypted, revision=1))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Настройки уже изменились") from None
    return public_config(config, revision + 1)
