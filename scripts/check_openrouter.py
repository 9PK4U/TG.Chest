"""Smoke-test embeddings and answer generation without exposing the API key."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.ai import ai
from app.config import get_settings


async def main():
    s = get_settings()
    try:
        vectors = await ai.embeddings(["TG.Chest connection test"])
        if not vectors:
            raise ValueError("Missing API key")
        print(f"Embeddings OK: {s.embedding_model}, dimensions={len(vectors[0])}")
        result = await ai.answer("Назови код проверки", [{"title": "Проверка подключения", "excerpt": "Код проверки: CHEST_OK."}])
        if not result or "CHEST_OK" not in result:
            raise ValueError("Answer not grounded")
        print(f"Grounded answer OK: {s.llm_model}")
    except Exception as exc:
        response = getattr(exc, "response", None)
        print(f"OpenRouter check failed: {type(exc).__name__}; status={getattr(response, 'status_code', 'n/a')}")
        raise SystemExit(1)


asyncio.run(main())
