import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy import delete, select

from .ai import ai, provider
from .backups import BackupProtected
from .config import get_settings
from .db import SessionLocal
from .models import Attachment, Chunk, Entry, Job
from .services import chunk_text, enqueue
from .storage import delete_disk, extract_safely, read_stage, upload_disk

log = logging.getLogger("chest.worker")


async def index_entry(db, identifier: str):
    entry = db.scalar(select(Entry).where(Entry.id == identifier).with_for_update())
    if not entry or entry.kind == "secret":
        return
    attachments = db.scalars(select(Attachment).where(Attachment.entry_id == identifier)).all()
    text = "\n".join([entry.title, entry.body, " ".join(entry.tags)] +
                      [f"{a.filename}\n{a.extracted_text}" for a in attachments])
    pieces = chunk_text(text)
    # Save lexical chunks even if the provider fails, then retry semantic indexing.
    db.execute(delete(Chunk).where(Chunk.entry_id == identifier))
    rows = [Chunk(entry_id=entry.id, user_id=entry.user_id, text=p) for p in pieces]
    db.add_all(rows)
    db.flush()
    model = provider(entry.ai_policy, db)
    try:
        for offset in range(0, len(rows), 32):
            batch = rows[offset:offset + 32]
            vectors = await model.embeddings([r.text for r in batch]) if model else None
            if vectors:
                for row, vector in zip(batch, vectors):
                    row.embedding = vector
                    row.model = model.embedding_identity
        entry.index_status = "ready" if any(r.embedding is not None for r in rows) else "text"
    except Exception:
        entry.index_status = "failed"
        db.commit()
        raise


async def upload_attachment(db, identifier: str):
    attachment = db.get(Attachment, identifier)
    if not attachment:
        return
    # Lock its parent before IO so concurrent deletion cannot orphan a remote upload.
    entry = db.scalar(select(Entry).where(Entry.id == attachment.entry_id).with_for_update())
    if not entry:
        return
    encrypted = Path(attachment.stage_path).read_bytes()
    content = await asyncio.to_thread(read_stage, attachment.stage_path)
    extracted = await extract_safely(content, attachment.filename)
    remote_path = await upload_disk(identifier, encrypted) if attachment.storage == "yandex" else ""
    attachment.extracted_text = extracted
    attachment.storage_path = remote_path
    attachment.status = "ready"
    stage_path = attachment.stage_path
    if remote_path:
        attachment.stage_path = ""
        enqueue(db, "delete_local", stage_path)
    entry.index_status = "pending"
    enqueue(db, "index", entry.id)


def claim_job() -> str | None:
    now = time.time()
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.status.in_(["pending", "running"]), Job.run_at <= now,
                                          Job.locked_until < now).order_by(Job.run_at).with_for_update(skip_locked=True).limit(1))
        if not job:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_until = now + 900
        identifier = job.id
        db.commit()
        return identifier


async def run_job(identifier: str):
    from .ingestion import AttachmentsPending, process_capture
    with SessionLocal() as db:
        job = db.get(Job, identifier)
        try:
            if job.kind == "index":
                await index_entry(db, job.target_id)
            elif job.kind == "upload":
                await upload_attachment(db, job.target_id)
            elif job.kind == "delete_disk":
                await delete_disk(job.target_id)
            elif job.kind == "organize":
                await process_capture(db, job.target_id)
            elif job.kind == "delete_local":
                target = Path(job.target_id).resolve()
                staging = (get_settings().data_dir / "staging").resolve()
                if target.parent != staging:
                    raise ValueError("Invalid cleanup path")
                target.unlink(missing_ok=True)
            else:
                raise ValueError("Unknown job")
            # Delete completed jobs instead of retaining sensitive text/error payloads.
            db.delete(job)
            db.commit()
        except (BackupProtected, AttachmentsPending) as exc:
            db.rollback()
            job = db.get(Job, identifier)
            job.status = "pending"
            job.run_at = time.time() + (10 if isinstance(exc, AttachmentsPending) else 86400)
            job.locked_until = 0
            job.attempts = max(0, job.attempts - 1)
            job.error = ""
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, identifier)
            job.error = type(exc).__name__
            job.status = "failed" if job.attempts >= 8 else "pending"
            job.run_at = time.time() + min(3600, 10 * 2 ** job.attempts)
            job.locked_until = 0
            if job.kind == "upload":
                attachment = db.get(Attachment, job.target_id)
                if attachment:
                    attachment.status = "failed" if job.status == "failed" else "retrying"
            if job.kind == "organize":
                from .models import Capture
                capture = db.get(Capture, job.target_id)
                if capture:
                    capture.status = "failed" if job.status == "failed" else "queued"
                    capture.error = type(exc).__name__
            db.commit()
            log.warning("Job %s failed (%s); state=%s", identifier, job.error, job.status)


async def main():
    get_settings().validate_runtime()
    while True:
        try:
            identifier = claim_job()
            if identifier:
                await run_job(identifier)
            else:
                await asyncio.sleep(2)
        except Exception as exc:
            log.error("Worker error: %s", type(exc).__name__)
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
