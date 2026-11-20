import math
import re

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .ai import ai, provider
from .config import get_settings
from .models import Chunk, Entry
from .services import folder_descendants


def tokens(value: str):
    return set(re.findall(r"[\w]+", value.lower()))


async def search(db: Session, user_id: int, query: str, folder_id=None, answer=True, ai_policy="external") -> dict:
    s = get_settings()
    warnings = []
    filters = [Chunk.user_id == user_id, Entry.user_id == user_id, Entry.kind != "secret"]
    model = provider(ai_policy, db)
    if ai_policy == "external":
        filters.append(Entry.ai_policy == "external")
    elif ai_policy == "trusted":
        filters.append(Entry.ai_policy.in_(["external", "trusted"]))
    if folder_id:
        filters.append(Entry.folder_id.in_(folder_descendants(db, user_id, folder_id)))
    base = select(Chunk).join(Entry, Entry.id == Chunk.entry_id).where(*filters)
    if db.bind.dialect.name == "postgresql":
        ts = func.to_tsvector(text("'russian'"), Chunk.text)
        q = func.websearch_to_tsquery(text("'russian'"), query)
        lexical = db.scalars(base.where(ts.op("@@")(q)).order_by(func.ts_rank_cd(ts, q).desc()).limit(60)).all()
    else:
        qt = tokens(query)
        # SQLite lower()/LIKE only case-fold ASCII; Python case-fold handles Russian.
        candidates = db.scalars(base.order_by(Entry.updated_at.desc()).limit(5000)).all()
        lexical = sorted((c for c in candidates if tokens(c.text) & qt),
                         key=lambda c: len(tokens(c.text) & qt) / max(len(qt), 1), reverse=True)[:60]
    semantic = []
    try:
        vectors = await model.embeddings([query]) if model else None
        if vectors:
            vector = vectors[0]
            semantic_query = base.where(Chunk.model == model.embedding_identity, Chunk.embedding.is_not(None))
            if db.bind.dialect.name == "postgresql":
                distance = Chunk.embedding.cosine_distance(vector)
                semantic = db.scalars(semantic_query.where(distance < 0.65).order_by(distance).limit(60)).all()
            else:
                candidates = db.scalars(semantic_query.limit(5000)).all()
                def cosine(c):
                    a = c.embedding
                    return sum(x * y for x, y in zip(a, vector)) / max(1e-12, math.sqrt(sum(x*x for x in a) * sum(x*x for x in vector)))
                semantic = sorted((c for c in candidates if cosine(c) > .35), key=cosine, reverse=True)[:60]
    except Exception:
        warnings.append("Семантический поиск временно недоступен; показаны текстовые совпадения.")
    if model and not model.configured:
        warnings.append("Модель выбранного контура не подключена. Работает локальный текстовый поиск.")
    if ai_policy == "external" and model and not model.config.connection_ready:
        warnings.append("OpenRouter не подключён: работает текстовый поиск.")
    scores, chunks = {}, {}
    for ranking in (lexical, semantic):
        for rank, chunk in enumerate(ranking):
            chunks[chunk.id] = chunk
            scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (60 + rank + 1)
    ordered = [chunks[k] for k in sorted(scores, key=scores.get, reverse=True)]
    # One source per entry; append several matching passages for a useful grounded answer.
    grouped = {}
    for chunk in ordered:
        grouped.setdefault(chunk.entry_id, []).append(chunk.text)
    results = []
    top_k = model.config.top_k if model else 8
    for entry_id, passages in list(grouped.items())[:max(12, top_k * 2)]:
        entry = db.get(Entry, entry_id)
        results.append({"id": entry.id, "title": entry.title, "kind": entry.kind,
                        "folder_id": entry.folder_id, "excerpt": "\n…\n".join(passages[:3])[:4200]})
    try:
        order = await model.rerank(query, [r["title"] + "\n" + r["excerpt"] for r in results]) if results and model else list(range(len(results)))
        results = [results[i] for i in order][:top_k]
    except Exception:
        warnings.append("Реранкер недоступен; сохранён порядок гибридного поиска.")
        results = results[:top_k]
    generated = None
    if answer and model:
        try:
            generated = await model.answer(query, results)
        except Exception:
            warnings.append("Ответ ИИ временно недоступен; источники найдены.")
    return {"results": results, "answer": generated, "warnings": warnings,
            "mode": "hybrid" if semantic else "text"}
