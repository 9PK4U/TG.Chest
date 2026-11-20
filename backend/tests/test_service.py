import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.ai import ai
from app.auth import validate_init_data
from app.config import get_settings
from app.models import Attachment, Chunk, Entry, Job, User
from app.services import enqueue
from app.storage import read_stage
from app.worker import claim_job, index_entry, run_job


def signed(user_id=1, age=0):
    fields = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": user_id, "first_name": "Owner"})}
    key = hmac.new(b"WebAppData", get_settings().bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def cipher():
    return {"v": 1, "iv": base64.b64encode(b"a" * 12).decode(), "ciphertext": base64.b64encode(b"x" * 32).decode()}


def setup_vault(client):
    return client.post("/api/vault", json={"salt": base64.b64encode(b"s" * 16).decode(), "verifier": cipher()})


def create(client, **kwargs):
    response = client.post("/api/entries", json={"title": "Запись", "body": "текст", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def drain(maximum=30):
    for _ in range(maximum):
        identifier = claim_job()
        if not identifier:
            break
        asyncio.run(run_job(identifier))


def test_telegram_auth_and_expiry(client, monkeypatch):
    s = get_settings()
    assert validate_init_data(signed(), s.bot_token, 60)["id"] == 1
    for invalid in [signed(age=1000), signed(age=-1000), signed() + "&auth_date=1", signed().replace("Owner", "Attacker")]:
        with pytest.raises(HTTPException):
            validate_init_data(invalid, s.bot_token, 60)
    monkeypatch.setattr(s, "dev_auth", False)
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "tma " + signed()}).status_code == 200
    assert client.get("/api/me", headers={"Authorization": "tma " + signed(2)}).status_code == 403


def test_revision_and_pagination(client):
    note = create(client, title="Первая")
    for i in range(4):
        create(client, title=str(i))
    response = client.get("/api/entries?limit=2&offset=2").json()
    assert len(response["items"]) == 2 and response["total"] == 5
    payload = {"title": "Правка", "body": "обновление", "revision": 1}
    assert client.put(f"/api/entries/{note['id']}", json=payload).json()["revision"] == 2
    assert client.put(f"/api/entries/{note['id']}", json=payload).status_code == 409


def test_folders_cycles_and_preserving_notes(client):
    parent = client.post("/api/folders", json={"name": "Работа"}).json()["id"]
    child = client.post("/api/folders", json={"name": "Проекты", "parent_id": parent}).json()["id"]
    note = create(client, folder_id=child)
    assert client.put(f"/api/folders/{parent}", json={"name": "Работа", "parent_id": child}).status_code == 400
    assert client.delete(f"/api/folders/{child}").status_code == 200
    assert client.get(f"/api/entries/{note['id']}").json()["folder_id"] == parent
    assert client.delete(f"/api/folders/{parent}").status_code == 200
    assert client.get(f"/api/entries/{note['id']}").json()["folder_id"] is None


def test_owner_isolation(client, database, monkeypatch):
    note = create(client)
    folder = client.post("/api/folders", json={"name": "Приватный"}).json()["id"]
    monkeypatch.setattr(get_settings(), "dev_user_id", 2)
    assert client.get("/api/me").status_code == 200
    assert client.get(f"/api/entries/{note['id']}").status_code == 404
    assert client.delete(f"/api/entries/{note['id']}").status_code == 404
    assert client.get("/api/entries").json()["total"] == 0
    assert client.post("/api/entries", json={"title": "Чужая", "folder_id": folder}).status_code == 404
    drain()
    assert client.post("/api/search", json={"query": "текст"}).json()["results"] == []


def test_vault_is_opaque_and_excluded(client, database, monkeypatch):
    assert setup_vault(client).status_code == 201
    assert setup_vault(client).status_code == 409
    secret = create(client, title="SecretUnique", kind="secret", body="", secret=cipher())
    assert client.post("/api/entries", json={"title": "bad", "kind": "secret", "body": "PLAIN_PASSWORD", "secret": cipher()}).status_code == 422
    assert client.post(f"/api/entries/{secret['id']}/files", files={"file": ("test.txt", b"private")}).status_code == 400
    assert client.get("/api/entries").json()["total"] == 0
    assert client.get("/api/entries?view=vault").json()["total"] == 1
    seen = []
    async def fake_embeddings(texts):
        seen.extend(texts)
        return None
    monkeypatch.setattr(ai, "embeddings", fake_embeddings)
    with database() as db:
        asyncio.run(index_entry(db, secret["id"]))
        assert not db.scalars(select(Chunk)).all()
        # Defense in depth even if a stale/private chunk somehow appears in the DB.
        db.add(Chunk(entry_id=secret["id"], user_id=1, text="SecretUnique private password"))
        db.commit()
    assert client.post("/api/search", json={"query": "SecretUnique"}).json()["results"] == []
    assert seen == ["SecretUnique"]


def test_encrypted_file_index_download_and_delete(client, database):
    note = create(client)
    response = client.post(f"/api/entries/{note['id']}/files", files={"file": ("../secret.txt", "Инструкция по настройке сервера".encode(), "text/plain")})
    assert response.status_code == 201
    file_id = response.json()["id"]
    with database() as db:
        attachment = db.get(Attachment, file_id)
        path = Path(attachment.stage_path)
        assert attachment.filename == "secret.txt"
        assert "Инструкция".encode() not in path.read_bytes()
        assert read_stage(str(path)).decode().startswith("Инструкция")
    drain()
    result = client.post("/api/search", json={"query": "настройке сервера"}).json()
    assert result["results"][0]["id"] == note["id"]
    downloaded = client.get(f"/api/files/{file_id}")
    assert downloaded.status_code == 200 and "Инструкция" in downloaded.content.decode()
    assert downloaded.headers["cache-control"] == "no-store"
    assert client.delete(f"/api/entries/{note['id']}").status_code == 200
    drain()
    assert not path.exists()
    assert client.get(f"/api/files/{file_id}").status_code == 404
    assert not client.post("/api/search", json={"query": "сервера"}).json()["results"]


def test_disk_failure_retries_preserving_stage(client, database, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_backend", "yandex")
    note = create(client)
    result = client.post(f"/api/entries/{note['id']}/files", files={"file": ("file.txt", b"retain me")})
    async def fail(*args):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr("app.worker.upload_disk", fail)
    drain()
    with database() as db:
        attachment = db.get(Attachment, result.json()["id"])
        assert attachment.status == "retrying" and Path(attachment.stage_path).exists()
        job = db.scalar(select(Job).where(Job.kind == "upload"))
        assert job.status == "pending" and job.attempts == 1
        assert job.error == "RuntimeError"
    assert client.get(f"/api/files/{result.json()['id']}").content == b"retain me"


def test_embedding_failure_keeps_lexical_search(client, database, monkeypatch):
    note = create(client, title="Путешествие", body="Билеты на самолет")
    async def fail(*args):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(ai, "embeddings", fail)
    drain()
    result = client.post("/api/search", json={"query": "билеты"}).json()
    assert result["results"][0]["id"] == note["id"]
    assert result["warnings"]
    with database() as db:
        assert db.get(Entry, note["id"]).index_status == "failed"


def test_semantic_search_and_folder_scope(client, database, monkeypatch):
    a = client.post("/api/folders", json={"name": "A"}).json()["id"]
    b = client.post("/api/folders", json={"name": "B", "parent_id": a}).json()["id"]
    note = create(client, title="Звезда", body="Полярная", folder_id=b)
    other = create(client, title="Река", body="Енисей")
    vector = [1.] + [0.] * (get_settings().embedding_dimensions - 1)
    async def fake(texts):
        return [vector for _ in texts]
    monkeypatch.setattr(ai, "embeddings", fake)
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test")
    drain()
    result = client.post("/api/search", json={"query": "небо", "folder_id": a}).json()
    assert result["mode"] == "hybrid"
    assert [r["id"] for r in result["results"]] == [note["id"]]


def test_export_includes_encrypted_vault_and_file_metadata(client):
    assert setup_vault(client).status_code == 201
    create(client, kind="secret", body="", secret=cipher())
    data = client.get("/api/export").json()
    assert data["format"] == "tg.chest.v1" and data["vault"]["salt"]
    assert data["entries"][0]["secret"] == cipher()


def test_vault_hint_setup_update_clear_and_legacy_vault(client, database, monkeypatch):
    assert client.put('/api/vault/hint', json={'hint': 'test'}).status_code == 409
    setup = {"salt": base64.b64encode(b"s" * 16).decode(), "verifier": cipher(), "hint": "  Личная ассоциация  "}
    assert client.post('/api/vault', json=setup).status_code == 201
    before = client.get('/api/me').json()['vault']
    assert before['hint'] == 'Личная ассоциация'
    assert client.put('/api/vault/hint', json={'hint': 'Новая подсказка'}).status_code == 200
    updated = client.get('/api/me').json()['vault']
    assert updated == {**before, 'hint': 'Новая подсказка'}
    assert client.get('/api/export').json()['vault']['hint'] == 'Новая подсказка'
    assert client.put('/api/vault/hint', json={'hint': 'x' * 301}).status_code == 422
    assert client.put('/api/vault/hint', json={'hint': ''}).status_code == 200
    assert client.get('/api/me').json()['vault'] == {**before, 'hint': ''}
    # Older JSON vault values have no hint; adding it never changes crypto metadata.
    with database() as db:
        db.get(User, 1).vault = {k: v for k, v in before.items() if k != 'hint'}
        db.commit()
    assert client.put('/api/vault/hint', json={'hint': 'Совместимо'}).status_code == 200
    assert client.get('/api/me').json()['vault'] == {**before, 'hint': 'Совместимо'}
    monkeypatch.setattr(get_settings(), 'dev_user_id', 2)
    assert client.put('/api/vault/hint', json={'hint': 'Other user'}).status_code == 409
    with database() as db:
        assert db.get(User, 1).vault['hint'] == 'Совместимо'
