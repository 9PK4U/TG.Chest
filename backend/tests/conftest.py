import os

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite://")
os.environ["ENVIRONMENT"] = "development"
os.environ["DEV_AUTH"] = "true"
os.environ["DEV_USER_ID"] = "1"
os.environ["ALLOWED_TELEGRAM_IDS"] = "1"
os.environ["BOT_TOKEN"] = "123456:TEST_TOKEN_FOR_TESTS_ONLY"
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["LLM_MODEL"] = ""
os.environ["RERANK_MODEL"] = ""
os.environ["STORAGE_BACKEND"] = "local"
os.environ["YANDEX_DISK_TOKEN"] = ""
os.environ["BACKUPS_ENABLED"] = "false"
os.environ["TRUSTED_AI_BASE_URL"] = ""
os.environ["TRUSTED_AI_MODEL"] = ""
os.environ["TRUSTED_EMBEDDING_MODEL"] = ""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app import models
from app.main import app, recent
from app import worker


@pytest.fixture
def database(monkeypatch, tmp_path):
    url = os.environ["DATABASE_URL"]
    engine = create_engine(url, **({"connect_args": {"check_same_thread": False}, "poolclass": StaticPool} if url == "sqlite://" else {}))
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "file_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr("app.ai_config.SessionLocal", factory)
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    recent.clear()
    yield factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(database):
    with TestClient(app) as client:
        client.headers["Authorization"] = "Dev local"
        assert client.get("/api/me").status_code == 200
        yield client
