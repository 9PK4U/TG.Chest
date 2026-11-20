import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Attachment, Capture, Chunk, Entry, Folder, Job, User, uid
from .schemas import EntryCreate
from .storage import stage_file


def owned(db: Session, model, identifier: str, user_id: int):
    obj = db.get(model, identifier)
    if not obj or obj.user_id != user_id:
        raise HTTPException(404, "Не найдено")
    return obj


def check_folder(db: Session, folder_id: str | None, user_id: int):
    if folder_id:
        owned(db, Folder, folder_id, user_id)


def folder_descendants(db: Session, user_id: int, folder_id: str) -> list[str]:
    owned(db, Folder, folder_id, user_id)
    folders = db.scalars(select(Folder).where(Folder.user_id == user_id)).all()
    result = {folder_id}
    while True:
        more = {f.id for f in folders if f.parent_id in result} - result
        if not more:
            return list(result)
        result |= more


def enqueue(db: Session, kind: str, target_id: str):
    db.add(Job(kind=kind, target_id=target_id))


def create_entry(db: Session, user_id: int, data: EntryCreate, source="miniapp", message_id=None) -> Entry:
    check_folder(db, data.folder_id, user_id)
    if data.kind == "secret" and not db.get(User, user_id).vault:
        raise HTTPException(409, "Сначала настройте сейф")
    entry = Entry(user_id=user_id, **data.model_dump(exclude={"secret"}),
                  secret=data.secret.model_dump() if data.secret else None,
                  source=source, telegram_message_id=message_id,
                  index_status="excluded" if data.kind == "secret" else "pending")
    db.add(entry)
    db.flush()
    if data.kind != "secret":
        enqueue(db, "index", entry.id)
    db.commit()
    return entry


def entry_dict(db: Session, entry: Entry, full=False) -> dict:
    result = {k: getattr(entry, k) for k in ["id", "title", "kind", "folder_id", "tags", "favorite", "created_at",
              "updated_at", "revision", "index_status", "source"]}
    result["preview"] = entry.body[:240] if entry.kind != "secret" else "Зашифровано мастер-паролем"
    result["attachments"] = [{"id": a.id, "filename": a.filename, "mime": a.mime,
                              "size": a.size, "status": a.status} for a in db.scalars(
                                  select(Attachment).where(Attachment.entry_id == entry.id)).all()]
    if full:
        result.update(body=entry.body, secret=entry.secret)
    result["ai_policy"] = entry.ai_policy
    result["capture_id"] = entry.capture_id
    return result


def add_attachment(db: Session, entry: Entry, filename: str, mime: str, content: bytes):
    if entry.kind == "secret":
        raise HTTPException(400, "Файлы нельзя прикреплять к секретам")
    s = get_settings()
    if len(content) > s.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"Максимальный размер файла — {s.max_file_mb} МБ")
    identifier = uid()
    # Never use a user-supplied name as a filesystem path.
    name = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").split("/")[-1])[:255] or "file"
    path = stage_file(identifier, content)
    attachment = Attachment(id=identifier, entry_id=entry.id, filename=name, mime=mime[:120],
                            size=len(content), stage_path=path, storage=s.storage_backend)
    try:
        db.add(attachment)
        enqueue(db, "upload", identifier)
        db.commit()
    except Exception:
        db.rollback()
        Path(path).unlink(missing_ok=True)
        raise
    return attachment


def delete_entry(db: Session, entry: Entry):
    for record in db.scalars(select(Capture).where(Capture.user_id == entry.user_id,
            (Capture.target_id == entry.id) | (Capture.id == entry.capture_id))):
        others = db.scalars(select(Entry).where(Entry.user_id == entry.user_id,
            Entry.id != entry.id, Entry.capture_id == record.id)).all()
        if others:
            record.target_id = others[0].id
            record.result_ids = [e.id for e in others]
        else:
            db.delete(record)
    for a in db.scalars(select(Attachment).where(Attachment.entry_id == entry.id)):
        if a.storage == "yandex":
            remote = a.storage_path or f"{get_settings().yandex_disk_root.rstrip('/')}/{a.id}.enc"
            enqueue(db, "delete_disk", remote)
        if a.stage_path:
            enqueue(db, "delete_local", a.stage_path)
    db.execute(delete(Chunk).where(Chunk.entry_id == entry.id))
    db.execute(delete(Attachment).where(Attachment.entry_id == entry.id))
    db.delete(entry)
    db.commit()


def chunk_text(text: str, size=1400, overlap=200) -> list[str]:
    text = text.strip()
    return [text[i:i + size] for i in range(0, len(text), size - overlap) if text[i:i + size].strip()]
