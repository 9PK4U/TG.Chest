"""Authenticated production smoke check, using only temporary owner records."""
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from dotenv import dotenv_values


def main():
    config = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    token = config["BOT_TOKEN"]
    owner = int(config["ALLOWED_TELEGRAM_IDS"].split(",")[0])
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": owner, "first_name": "pk4u"})}
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    authorization = "tma " + urlencode(fields)
    with httpx.Client(base_url=config["PUBLIC_URL"] + "/api", headers={"Authorization": authorization}, timeout=180) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise ValueError(f"{method} {path}: HTTP {response.status_code}")
            return response
        parent = child = entry = None
        try:
            me = request("GET", "/me").json()
            print("Production file storage: " + me["integrations"]["storage"])
            parent = request("POST", "/folders", json={"name": "Проверка развёртывания"}).json()["id"]
            child = request("POST", "/folders", json={"name": "Временный подраздел", "parent_id": parent}).json()["id"]
            entry = request("POST", "/entries", json={"title": "Проверка подключения Chest", "folder_id": child,
                            "body": "Код проверки развёртывания: CHEST_DEPLOY_OK."}).json()["id"]
            payload = b"TG.Chest encrypted-file verification"
            attachment = request("POST", f"/entries/{entry}/files", files={"file": ("check.txt", payload, "text/plain")}).json()["id"]
            for _ in range(40):
                state = request("GET", f"/entries/{entry}").json()
                if state["index_status"] == "ready" and state["attachments"][0]["status"] == "ready":
                    break
                time.sleep(2)
            else:
                raise ValueError("Indexing or file upload did not finish")
            assert request("GET", f"/files/{attachment}").content == payload
            print("HTTPS authenticated upload/download: OK")
            result = request("POST", "/search", json={"query": "Какой код проверки развёртывания?", "folder_id": parent}).json()
            assert any(r["id"] == entry for r in result["results"])
            assert result["answer"] and "CHEST_DEPLOY_OK" in result["answer"]
            assert result["mode"] == "hybrid"
            print("Production pgvector + OpenRouter grounded answer + nested folder scope: OK")
        finally:
            for path in [f"/entries/{entry}" if entry else None, f"/folders/{child}" if child else None, f"/folders/{parent}" if parent else None]:
                if path:
                    request("DELETE", path)
            print("Temporary smoke-check records removed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, ValueError) else f"Live check failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
