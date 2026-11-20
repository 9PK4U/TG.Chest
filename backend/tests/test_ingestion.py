import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.ai import ai, trusted_ai
from app.config import get_settings
from app.ingestion import unseal
from app.models import Attachment, Capture, Chunk, Entry, Folder, Job
from app.schemas import OrganizedNotes, Replacement
from app.worker import claim_job, run_job


def drain():
    for _ in range(40):
        identifier = claim_job()
        if not identifier:
            break
        asyncio.run(run_job(identifier))


def configure(monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "fake-key")
    monkeypatch.setattr(get_settings(), "llm_model", "fake-model")
    monkeypatch.setattr(ai, "embeddings", AsyncMock(return_value=None))


def test_intake_splits_and_keeps_encrypted_original(client, database, monkeypatch):
    configure(monkeypatch)
    original = "Встреча завтра в 12. Идея: сделать домашнюю библиотеку."
    call = AsyncMock(return_value=OrganizedNotes(notes=[
        {"title": "Встреча", "body": "Завтра в 12", "folder_path": ["Работа"]},
        {"title": "Библиотека", "body": "Сделать домашнюю библиотеку", "tags": ["идея"]}]))
    monkeypatch.setattr(ai, "structured", call)
    created = client.post("/api/intake", json={"text": original}).json()
    drain()
    with database() as db:
        capture = db.get(Capture, created["capture_id"])
        assert capture.status == "applied" and len(capture.result_ids) == 2
        assert original not in capture.payload
        assert unseal(capture)["before"]["body"] == original
        assert db.get(Entry, capture.result_ids[0]).title == "Встреча"
        assert db.get(Entry, capture.result_ids[1]).capture_id == capture.id
        assert db.scalar(select(Folder)).name == "Работа"
    sources = client.get(f"/api/entries/{created['id']}/sources").json()
    assert sources[0]["original"]["body"] == original
    assert call.call_args.args[1]["text"] == original
    drain()
    assert call.await_count == 1
    with database() as db:
        derived = db.get(Capture, created["capture_id"]).result_ids[1]
    assert client.delete(f"/api/entries/{created['id']}").status_code == 200
    assert client.get(f"/api/entries/{derived}/sources").json()[0]["original"]["body"] == original
    assert client.delete(f"/api/entries/{derived}").status_code == 200
    with database() as db:
        assert db.get(Capture, created["capture_id"]) is None


def test_sensitive_input_never_reaches_external_even_when_trusted_missing(client, database, monkeypatch):
    configure(monkeypatch)
    external = AsyncMock(side_effect=AssertionError("Private text leaked"))
    monkeypatch.setattr(ai, "structured", external)
    monkeypatch.setattr(ai, "embeddings", external)
    note = client.post("/api/intake", json={"text": "PRIVATE_DATA", "ai_policy": "trusted"}).json()
    drain()
    with database() as db:
        record = db.get(Capture, note["capture_id"])
        assert record.status == "waiting" and unseal(record)["before"]["body"] == "PRIVATE_DATA"
        assert db.get(Entry, note["id"]).body == "PRIVATE_DATA"
    assert not external.called
    assert client.post(f"/api/entries/{note['id']}/assist", json={"instruction": "исправь", "revision": 1, "start": 0, "end": 2}).status_code == 503
    assert not external.called


def test_private_file_and_context_are_absent_from_external_rag(client, database, monkeypatch):
    configure(monkeypatch)
    public = client.post("/api/entries", json={"title": "Общее", "body": "ключевое слово"}).json()
    private = client.post("/api/entries", json={"title": "PRIVATE_TITLE", "body": "PRIVATE_BODY ключевое", "ai_policy": "trusted"}).json()
    client.post(f"/api/entries/{private['id']}/files", files={"file": ("private.txt", "PRIVATE_FILE ключевое".encode())})
    drain()
    with database() as db:
        # Stale vectors/chunks must not bypass entry privacy at query time.
        db.add(Chunk(user_id=1, entry_id=private["id"], text="PRIVATE_STALE ключевое"))
        db.commit()
    answer = AsyncMock(return_value="ответ")
    rerank = AsyncMock(side_effect=lambda query, texts: list(range(len(texts))))
    monkeypatch.setattr(ai, "answer", answer)
    monkeypatch.setattr(ai, "rerank", rerank)
    result = client.post("/api/search", json={"query": "ключевое"}).json()
    assert [row["id"] for row in result["results"]] == [public["id"]]
    assert "PRIVATE" not in repr(answer.call_args) + repr(rerank.call_args)
    assert "PRIVATE" not in repr(ai.embeddings.call_args_list)
    count = ai.embeddings.await_count
    result = client.post("/api/search", json={"query": "PRIVATE_BODY", "ai_policy": "local"}).json()
    assert result["results"][0]["id"] == private["id"]
    assert ai.embeddings.await_count == count


def test_edit_changes_only_selected_span_and_is_idempotent(client, database, monkeypatch):
    configure(monkeypatch)
    body = "😀 Начало. Дата: 22 октября. Конец без изменений."
    note = client.post("/api/entries", json={"title": "План", "body": body}).json()
    start = body.index("22 октября")
    monkeypatch.setattr(ai, "structured", AsyncMock(return_value=Replacement(replacement="25 октября")))
    proposal = client.post(f"/api/entries/{note['id']}/assist", json={"instruction": "перенеси на 25 октября", "start": start, "end": start + len("22 октября"), "revision": 1})
    assert proposal.status_code == 200
    assert client.get(f"/api/entries/{note['id']}").json()["body"] == body
    identifier = proposal.json()["id"]
    result = client.post(f"/api/captures/{identifier}/apply").json()
    assert result["body"] == body.replace("22 октября", "25 октября") and result["revision"] == 2
    assert client.post(f"/api/captures/{identifier}/apply").status_code == 409
    assert client.get(f"/api/entries/{note['id']}/sources").json()[0]["original"]["body"] == body


def test_changed_note_or_policy_invalidates_proposals_and_queued_work(client, database, monkeypatch):
    configure(monkeypatch)
    model = AsyncMock(return_value=Replacement(replacement="new"))
    monkeypatch.setattr(ai, "structured", model)
    note = client.post("/api/intake", json={"text": "text"}).json()
    proposal = client.post(f"/api/entries/{note['id']}/assist", json={"instruction": "edit", "start": 0, "end": 4, "revision": 1}).json()
    result = client.put(f"/api/entries/{note['id']}", json={"title": "private", "body": "PRIVATE", "revision": 1, "ai_policy": "trusted"})
    assert result.status_code == 200
    drain()
    assert model.await_count == 1  # Only the explicitly requested, previously public edit.
    assert client.post(f"/api/captures/{proposal['id']}/apply").status_code == 409
    with database() as db:
        assert db.get(Capture, note["capture_id"]).status == "stale"
    # Old clients omitting ai_policy cannot accidentally re-enable external access.
    updated = client.put(f"/api/entries/{note['id']}", json={"title": "still private", "body": "PRIVATE", "revision": 2}).json()
    assert updated["ai_policy"] == "trusted"


def test_trusted_endpoint_is_separate_and_can_process_private_notes(client, database, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(get_settings(), "trusted_ai_base_url", "http://private-inference/v1")
    monkeypatch.setattr(get_settings(), "trusted_ai_model", "private-model")
    external = AsyncMock(side_effect=AssertionError("wrong provider"))
    monkeypatch.setattr(ai, "structured", external)
    trusted = AsyncMock(return_value=OrganizedNotes(notes=[{"title": "Private", "body": "PRIVATE"}]))
    monkeypatch.setattr(trusted_ai, "structured", trusted)
    note = client.post("/api/intake", json={"text": "PRIVATE", "ai_policy": "trusted"}).json()
    drain()
    assert trusted.await_count == 1 and not external.called
    assert client.get(f"/api/entries/{note['id']}").json()["ai_policy"] == "trusted"


def test_attachment_assignment_and_rejection_of_invented_ids(client, database, monkeypatch):
    configure(monkeypatch)
    note = client.post("/api/intake", json={"text": "Документ и идея", "defer": True}).json()
    file = client.post(f"/api/entries/{note['id']}/files", files={"file": ("plan.txt", b"Document contents")}).json()
    model = AsyncMock(return_value=OrganizedNotes(notes=[{"title": "Идея", "body": "Идея"},
        {"title": "Документ", "body": "Описание", "attachment_ids": [file["id"]]}]))
    monkeypatch.setattr(ai, "structured", model)
    capture = client.post(f"/api/entries/{note['id']}/organize").json()
    drain()
    with database() as db:
        record = db.get(Capture, capture["id"])
        assert record.status == "applied"
        assert db.get(Attachment, file["id"]).entry_id == record.result_ids[1]
    assert model.call_args.args[1]["files"][0]["text"] == "Document contents"
    other = client.post("/api/intake", json={"text": "Bad mapping"}).json()
    model.return_value = OrganizedNotes(notes=[{"title": "Bad", "body": "bad", "attachment_ids": [file["id"]]}])
    drain()
    assert client.get(f"/api/entries/{other['id']}").json()["body"] == "Bad mapping"


def test_local_mode_and_missing_trusted_vault_never_call_external(client, monkeypatch):
    external = AsyncMock(side_effect=AssertionError("leak"))
    monkeypatch.setattr(ai, "structured", external)
    monkeypatch.setattr(ai, "embeddings", external)
    note = client.post("/api/intake", json={"text": "LOCAL_PRIVATE", "ai_policy": "local"}).json()
    drain()
    assert not external.called
    result = client.post("/api/trusted-vault/assist", json={"instruction": "edit", "fields": {"login": "private", "password": "private", "url": "", "notes": ""}})
    assert result.status_code == 503 and not external.called
    assert client.get(f"/api/entries/{note['id']}/sources").json()[0]["status"] == "saved"


def test_capture_owner_isolation(client, monkeypatch):
    note = client.post("/api/intake", json={"text": "mine", "ai_policy": "local"}).json()
    monkeypatch.setattr(get_settings(), "dev_user_id", 2)
    assert client.get(f"/api/entries/{note['id']}/sources").status_code == 404
    assert client.post(f"/api/captures/{note['capture_id']}/retry").status_code == 404
    assert client.post(f"/api/captures/{note['capture_id']}/apply").status_code == 404


def test_trusted_transport_uses_only_its_endpoint_credentials_and_proxy(database, monkeypatch):
    import httpx
    s = get_settings()
    monkeypatch.setattr(s, "trusted_ai_base_url", "http://private-inference:11434/v1")
    monkeypatch.setattr(s, "trusted_ai_api_key", "private-key")
    monkeypatch.setattr(s, "openrouter_api_key", "external-key")
    monkeypatch.setattr(s, "openrouter_proxy", "http://external-proxy:2080")
    requests, options = [], []
    def handle(request):
        requests.append(request)
        return httpx.Response(503, json={"error": "unavailable"})
    real_client = httpx.AsyncClient
    def client(**kwargs):
        options.append(kwargs.copy())
        return real_client(**kwargs, transport=httpx.MockTransport(handle))
    monkeypatch.setattr("app.ai.httpx.AsyncClient", client)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(trusted_ai.request("chat/completions", {"messages": [{"content": "PRIVATE"}]}))
    assert len(requests) == 1 and requests[0].url.host == "private-inference"
    assert requests[0].headers["Authorization"] == "Bearer private-key"
    assert options[0]["proxy"] is None and options[0]["trust_env"] is False
