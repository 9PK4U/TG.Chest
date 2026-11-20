import asyncio
import io
import os
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from .config import get_settings


def cipher() -> Fernet:
    s = get_settings()
    key = s.file_encryption_key
    if not key:
        if s.environment == "production":
            raise RuntimeError("Missing encryption key")
        path = s.data_dir / "development.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        key = path.read_text().strip()
    return Fernet(key.encode())


def stage_file(identifier: str, content: bytes) -> str:
    path = get_settings().data_dir / "staging" / f"{identifier}.enc"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(cipher().encrypt(content))
    temporary.replace(path)
    return str(path)


def read_stage(path: str) -> bytes:
    return cipher().decrypt(Path(path).read_bytes())


async def disk_request(method: str, endpoint: str, **kwargs):
    s = get_settings()
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.request(method, f"https://cloud-api.yandex.net/v1/disk/{endpoint}",
                                        headers={"Authorization": f"OAuth {s.yandex_disk_token}"}, **kwargs)
        return response


async def upload_disk(identifier: str, encrypted: bytes) -> str:
    root = get_settings().yandex_disk_root.rstrip("/")
    # Create every parent so custom roots also work. A scoped app token can use app:/.
    prefix, tail = root.split(":/", 1)
    current = f"{prefix}:/"
    for part in filter(None, tail.split("/")):
        current = current.rstrip("/") + "/" + part
        result = await disk_request("PUT", "resources", params={"path": current})
        if result.status_code != 409:
            result.raise_for_status()
    path = f"{root}/{identifier}.enc"
    response = await disk_request("GET", "resources/upload", params={"path": path, "overwrite": "true"})
    response.raise_for_status()
    async with httpx.AsyncClient(timeout=180) as client:
        result = await client.put(response.json()["href"], content=encrypted)
        result.raise_for_status()
    return path


async def download_disk(path: str) -> bytes:
    response = await disk_request("GET", "resources/download", params={"path": path})
    response.raise_for_status()
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        response = await client.get(response.json()["href"])
        response.raise_for_status()
        return cipher().decrypt(response.content)


async def delete_disk(path: str):
    if get_settings().backups_enabled:
        from .backups import BackupProtected, DiskBackups
        def protected():
            with DiskBackups() as backups:
                return backups.attachment_protected(path)
        if await asyncio.to_thread(protected):
            raise BackupProtected()
    result = await disk_request("DELETE", "resources", params={"path": path, "permanently": "true"})
    if result.status_code != 404:
        result.raise_for_status()


def extract_text(content: bytes, filename: str) -> str:
    """Bound extraction size; images/scans/audio are retained without transcription."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".py", ".js", ".ts"}:
        return content[:500000].decode("utf-8", errors="replace")[:100000]
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        texts, size = [], 0
        for page in reader.pages[:100]:
            text = (page.extract_text() or "")[:20000]
            texts.append(text)
            size += len(text)
            if size >= 100000:
                break
        return "\n".join(texts)[:100000]
    if suffix == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)[:100000]
    return ""


async def extract_safely(content: bytes, filename: str) -> str:
    # Parsing happens outside the web process. Malformed files remain downloadable.
    try:
        return await asyncio.wait_for(asyncio.to_thread(extract_text, content, filename), timeout=30)
    except Exception:
        return ""
