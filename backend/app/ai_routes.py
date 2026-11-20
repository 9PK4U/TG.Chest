"""Owner-only model playground: draft probes never use actual archive content."""
import time
import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .ai import AI, provider
from .ai_config import Policy, ProviderTest, ProviderUpdate, effective, merge_update, public_config, save
from .auth import current_user
from .db import get_db
from .models import AIConfiguration, Capture, Chunk, Entry, User
from .schemas import OrganizedNotes
from .services import enqueue

router = APIRouter(prefix="/api/ai-settings", tags=["model configuration"])


def reindex_scope(db, policy):
    records = db.scalars(select(Entry).where(Entry.ai_policy == policy, Entry.kind != "secret")).all()
    for entry in records:
        db.execute(delete(Chunk).where(Chunk.entry_id == entry.id))
        entry.index_status = "pending"
        enqueue(db, "index", entry.id)
    return len(records)


@router.get("")
def settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {policy: public_config(*effective(policy, db)) for policy in ["external", "trusted"]}


@router.put("/{policy}")
def update_settings(policy: Policy, request: ProviderUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    previous, _ = effective(policy, db)
    result = save(policy, request, db)
    result["reindex_queued"] = 0
    if (previous.base_url, previous.embedding_model) != (request.base_url, request.embedding_model):
        result["reindex_queued"] = reindex_scope(db, policy)
        db.commit()
    return result


class ResetRequest(BaseModel):
    revision: int = Field(ge=0)


@router.post("/{policy}/reset")
def reset_settings(policy: Policy, request: ResetRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, revision = effective(policy, db)
    if revision != request.revision:
        raise HTTPException(409, "Настройки уже изменились. Перезагрузите страницу.")
    result = db.execute(delete(AIConfiguration).where(AIConfiguration.id == policy, AIConfiguration.revision == revision))
    if revision and not result.rowcount:
        raise HTTPException(409, "Настройки уже изменились")
    reindex_scope(db, policy)
    db.commit()
    return public_config(*effective(policy, db))


@router.post("/{policy}/resume")
def resume_intake(policy: Policy, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not provider(policy, db).configured:
        raise HTTPException(409, "Сначала подключите модель этого контура")
    records = db.scalars(select(Capture).where(Capture.user_id == user.id, Capture.policy == policy,
        Capture.mode == "organize", Capture.status.in_(["waiting", "failed"]))).all()
    count = 0
    for record in records:
        entry = db.get(Entry, record.target_id)
        if entry and entry.user_id == user.id and entry.ai_policy == policy:
            record.status, record.error = "queued", ""
            enqueue(db, "organize", record.id)
            count += 1
    db.commit()
    return {"queued": count}


@router.post("/{policy}/test")
async def test_provider(policy: Policy, request: ProviderTest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    config, _ = effective(policy, db)
    if request.draft:
        config = merge_update(config, request.draft)
    model = AI(trusted=policy == "trusted", config=config)
    def failure(code, message):
        return {"ok": False, "task": request.task, "code": code, "message": message}
    if not config.enabled:
        return failure("disabled", "Этот контур выключен. Включите его перед проверкой.")
    if not config.base_url:
        return failure("missing_url", "Укажите адрес API, например https://server.example/v1.")
    if not config.connection_ready:
        return failure("missing_key", "Для этого API нужен ключ. Укажите API-ключ.")
    required = {"chat": config.chat_model, "organize": config.organize_model or config.chat_model,
                "embeddings": config.embedding_model, "rerank": config.rerank_model}
    if request.task in required and not required[request.task]:
        labels = {"chat": "модель ответов", "organize": "модель разбора или модель ответов",
                  "embeddings": "модель эмбеддингов", "rerank": "модель реранкера"}
        return failure("missing_model", "Не выбрана " + labels[request.task] + ". Загрузите список моделей или впишите ID вручную.")
    started = time.monotonic()
    try:
        if request.task == "models":
            result = await model.request("models", method="GET")
            models = result.get("data", [])
            ids = sorted({str(row["id"])[:200] for row in models[:5000] if isinstance(row, dict) and row.get("id")})
            return {"ok": True, "models": ids}
        if request.task == "chat":
            if not config.chat_model:
                raise ValueError("Chat model is empty")
            result = await model.request("chat/completions", {"model": config.chat_model,
                "max_tokens": config.answer_max_tokens, "messages": [{"role": "user", "content": "Connection check. Reply OK."}]})
            choices = result.get("choices", [])
            content = choices[0].get("message", {}).get("content") if choices else None
            if not isinstance(content, str) or not content.strip():
                return failure("empty_answer", "Модель не вернула текст ответа. Увеличьте лимит токенов или выберите другую модель.")
        elif request.task == "organize":
            await model.structured("Создай одну заметку из тестового текста, верни notes.",
                                   {"text": "Проверка подключения Chest. Встреча в 15:00."}, OrganizedNotes)
        elif request.task == "embeddings":
            vectors = await model.embeddings(["Chest connection check"])
            if not vectors:
                raise ValueError("Embedding model is empty or disabled")
        elif request.task == "rerank":
            if not config.rerank_model:
                raise ValueError("Rerank model is empty")
            await model.rerank("connection", ["connection check", "unrelated example"])
        return {"ok": True, "task": request.task, "latency_ms": round((time.monotonic() - started) * 1000),
                "message": "Проверка прошла. Использовались только тестовые данные."}
    except Exception as exc:
        if isinstance(exc, httpx.TimeoutException):
            return failure("timeout", "Сервер модели не успел ответить. Увеличьте таймаут в разделе «Прокси, генерация и поиск».")
        if isinstance(exc, httpx.ConnectError):
            return failure("connection", "Не удалось подключиться к API. Проверьте домен, TLS и доступность адреса с VDS.")
        if isinstance(exc, ValidationError):
            return failure("invalid_json", "Модель ответила, но разбор не соответствует JSON-формату заметок. Попробуйте другую модель разбора.")
        status = getattr(getattr(exc, "response", None), "status_code", None)
        # Do not echo provider bodies, signed URLs or credentials.
        message = f"Провайдер вернул HTTP {status}. Проверьте адрес, ключ и модель." if status else (
            "Нет корректного ответа. Проверьте модель, адрес, прокси и таймаут; эмбеддер должен возвращать 1536 чисел.")
        return {"ok": False, "task": request.task, "message": message}
