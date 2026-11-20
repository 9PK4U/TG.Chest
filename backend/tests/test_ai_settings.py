import asyncio

from sqlalchemy import select

from app.ai import AI, provider
from app.ai_config import effective
from app.config import get_settings
from app.models import AIConfiguration, Capture, Chunk
from app.schemas import OrganizedNotes
from app.worker import claim_job, run_job


def payload(client, policy="external", **changes):
    current = client.get("/api/ai-settings").json()[policy]
    result = {k: v for k, v in current.items() if k not in {"api_key_set", "configured", "embedding_dimensions"}}
    result.update(api_key=None, clear_api_key=False)
    result.update(changes)
    return result


def test_encrypted_key_never_echoed_and_live_config_applies(client, database):
    data = payload(client, base_url="https://example.test/v1", chat_model="my-chat", organize_model="my-organizer")
    data["api_key"] = "PRIVATE_API_KEY"
    response = client.put("/api/ai-settings/external", json=data)
    assert response.status_code == 200
    assert "PRIVATE_API_KEY" not in response.text and response.json()["api_key_set"]
    assert client.get("/api/me").json()["integrations"]["llm"]
    assert "PRIVATE_API_KEY" not in client.get("/api/ai-settings").text
    with database() as db:
        stored = db.get(AIConfiguration, "external")
        assert "PRIVATE_API_KEY" not in stored.payload and "my-chat" not in stored.payload
        config, revision = effective("external", db)
        assert config.api_key == "PRIVATE_API_KEY" and revision == 1
        assert provider("external", db).config.organize_model == "my-organizer"
    # Blank field preserves the key, explicit deletion overrides even env credentials.
    response = client.put("/api/ai-settings/external", json=payload(client, chat_model="new-chat"))
    assert response.status_code == 200 and response.json()["api_key_set"]
    response = client.put("/api/ai-settings/external", json=payload(client, clear_api_key=True))
    assert response.status_code == 200 and not response.json()["api_key_set"]


def test_config_revisions_validation_and_reset(client, monkeypatch):
    original = payload(client)
    changed = {**original, "base_url": "http://local-model:8000/v1", "chat_model": "local"}
    first = client.put("/api/ai-settings/trusted", json=changed)
    assert first.status_code == 200
    assert client.put("/api/ai-settings/trusted", json=changed).status_code == 409
    assert client.put("/api/ai-settings/trusted", json=payload(client, "trusted", temperature=3)).status_code == 422
    assert client.put("/api/ai-settings/trusted", json=payload(client, "trusted", base_url="file:///etc/passwd")).status_code == 422
    assert client.put("/api/ai-settings/trusted", json=payload(client, "trusted", base_url="https://example.test/v1?api_key=secret")).status_code == 422
    assert client.put("/api/ai-settings/trusted", json=payload(client, "trusted", embedding_dimensions=42)).status_code == 422
    reset = client.post("/api/ai-settings/trusted/reset", json={"revision": first.json()["revision"]})
    assert reset.status_code == 200 and reset.json()["revision"] == 0 and not reset.json()["configured"]
    monkeypatch.setattr(get_settings(), "dev_auth", False)
    assert client.get("/api/ai-settings").status_code == 401


def test_draft_probe_and_catalog_use_synthetic_input_without_saving(client, database, monkeypatch):
    calls = []
    async def request(self, endpoint, body=None, method="POST"):
        calls.append((self.trusted, self.config.base_url, endpoint, body, method))
        if endpoint == "models":
            return {"data": [{"id": "test-model"}]}
        return {"choices": [{"message": {"content": "OK"}}]}
    monkeypatch.setattr(AI, "request", request)
    draft = payload(client, "trusted", base_url="http://private-model/v1", chat_model="local-model")
    result = client.post("/api/ai-settings/trusted/test", json={"task": "chat", "draft": draft})
    assert result.json()["ok"]
    assert calls[0][0] and calls[0][1] == "http://private-model/v1"
    assert "Connection check" in repr(calls[0][3])
    result = client.post("/api/ai-settings/trusted/test", json={"task": "models", "draft": draft})
    assert result.json()["models"] == ["test-model"] and calls[1][4] == "GET"
    with database() as db:
        assert not db.scalars(select(AIConfiguration)).all()


def test_provider_change_invalidates_only_its_vectors_and_requeues(client, database):
    public = client.post("/api/entries", json={"title": "public"}).json()
    private = client.post("/api/entries", json={"title": "private", "ai_policy": "trusted"}).json()
    with database() as db:
        db.add(Chunk(user_id=1, entry_id=public["id"], text="public"))
        db.add(Chunk(user_id=1, entry_id=private["id"], text="private"))
        before = provider("external", db).embedding_identity
        db.commit()
    result = client.put("/api/ai-settings/external", json=payload(client, embedding_model="new-embedding"))
    assert result.status_code == 200 and result.json()["reindex_queued"] == 1
    with database() as db:
        assert provider("external", db).embedding_identity != before
        assert not db.scalars(select(Chunk).where(Chunk.entry_id == public["id"])).all()
        assert db.scalars(select(Chunk).where(Chunk.entry_id == private["id"])).all()


def test_disabled_provider_cannot_leak_to_external_and_resume_uses_saved_trusted(client, database, monkeypatch):
    draft = payload(client, "trusted", base_url="http://private-model/v1", chat_model="local-model", enabled=False)
    assert client.put("/api/ai-settings/trusted", json=draft).status_code == 200
    note = client.post("/api/intake", json={"text": "PRIVATE_TEXT", "ai_policy": "trusted"}).json()
    for _ in range(10):
        job = claim_job()
        if job:
            asyncio.run(run_job(job))
    with database() as db:
        assert db.get(Capture, note["capture_id"]).status == "waiting"
    assert client.post("/api/ai-settings/trusted/resume").status_code == 409
    assert client.put("/api/ai-settings/trusted", json=payload(client, "trusted", enabled=True)).status_code == 200
    assert client.post("/api/ai-settings/trusted/resume").json()["queued"] == 1
    # Runtime follows saved settings rather than empty TRUSTED_AI_* in .env.
    with database() as db:
        trusted = provider("trusted", db)
        assert trusted.configured and trusted.config.base_url == "http://private-model/v1"
        assert provider("external", db).config.base_url != trusted.config.base_url


def test_embedding_probe_rejects_wrong_dimensions_and_hides_provider_errors(client, monkeypatch):
    async def request(self, endpoint, body=None, method="POST"):
        if endpoint == "embeddings":
            return {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}
        raise ValueError("PRIVATE_PROVIDER_ERROR_WITH_SECRET")
    monkeypatch.setattr(AI, "request", request)
    draft = payload(client, "trusted", base_url="http://local/v1", chat_model="model", embedding_model="embed")
    result = client.post("/api/ai-settings/trusted/test", json={"task": "embeddings", "draft": draft})
    assert not result.json()["ok"] and "1536" in result.json()["message"]
    result = client.post("/api/ai-settings/trusted/test", json={"task": "chat", "draft": draft})
    assert not result.json()["ok"] and "PRIVATE_PROVIDER" not in result.text


def test_bot_and_worker_proxies_are_independent(client, database, monkeypatch):
    import httpx
    data = payload(client, base_url="http://models/v1", proxy_url="http://worker-proxy:7890", bot_proxy_url="http://127.0.0.1:7890")
    assert client.put("/api/ai-settings/external", json=data).status_code == 200
    seen = []
    original = httpx.AsyncClient
    def make(**options):
        seen.append(options.pop("proxy"))
        return original(**options, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    monkeypatch.setattr("app.ai.httpx.AsyncClient", make)
    with database() as db:
        model = provider("external", db)
    asyncio.run(model.request("models", method="GET"))
    monkeypatch.setenv("CHEST_PROCESS_ROLE", "bot")
    asyncio.run(model.request("models", method="GET"))
    assert seen == ["http://worker-proxy:7890", "http://127.0.0.1:7890"]


def test_probe_explains_missing_model_without_calling_provider(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("Missing model must not make a request")
    monkeypatch.setattr(AI, "request", fail)
    draft = payload(client, "trusted", base_url="https://strata.example/v1", chat_model="", organize_model="")
    for task in ["chat", "organize"]:
        result = client.post("/api/ai-settings/trusted/test", json={"task": task, "draft": draft}).json()
        assert not result["ok"] and result["code"] == "missing_model" and "модел" in result["message"]
    draft["enabled"] = False
    assert client.post("/api/ai-settings/trusted/test", json={"task": "models", "draft": draft}).json()["code"] == "disabled"


def test_probe_rejects_empty_answer_and_distinguishes_timeout(client, monkeypatch):
    import httpx
    async def empty(*args, **kwargs):
        return {"choices": [{"message": {"content": "", "reasoning_content": "internal reasoning"}}]}
    monkeypatch.setattr(AI, "request", empty)
    draft = payload(client, "trusted", base_url="https://local.example/v1", chat_model="local")
    assert client.post("/api/ai-settings/trusted/test", json={"task": "chat", "draft": draft}).json()["code"] == "empty_answer"
    async def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("PRIVATE_URL_OR_KEY")
    monkeypatch.setattr(AI, "request", timeout)
    result = client.post("/api/ai-settings/trusted/test", json={"task": "chat", "draft": draft})
    assert result.json()["code"] == "timeout" and "PRIVATE_URL_OR_KEY" not in result.text
