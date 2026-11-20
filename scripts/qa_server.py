"""Isolated browser-test service, without real Telegram/AI/Disk credentials."""
import asyncio
import os
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
qa = root / "test-results" / "browser-data"
qa.mkdir(parents=True, exist_ok=True)
db_path = qa / "qa.db"
db_path.unlink(missing_ok=True)
os.environ.update({"DATABASE_URL": "sqlite:///" + db_path.as_posix(), "ENVIRONMENT": "development", "DEV_AUTH": "true",
    "DEV_USER_ID": "7", "BOT_TOKEN": "", "ALLOWED_TELEGRAM_IDS": "7", "OPENROUTER_API_KEY": "", "LLM_MODEL": "",
    "RERANK_MODEL": "", "STORAGE_BACKEND": "local", "YANDEX_DISK_TOKEN": "", "FILE_ENCRYPTION_KEY": "",
    "DATA_DIR": str(qa), "PUBLIC_URL": "http://127.0.0.1:8001"})
os.environ.update({"TRUSTED_AI_BASE_URL": "", "TRUSTED_AI_MODEL": "", "TRUSTED_EMBEDDING_MODEL": "", "BACKUPS_ENABLED": "false"})
from app.db import Base, engine
from app.main import app
from app.worker import main as worker
import uvicorn

Base.metadata.create_all(engine)

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

app.router.lifespan_context = lifespan
uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)
