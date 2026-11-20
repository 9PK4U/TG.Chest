"""Production smoke for free-form intake and private routing, using synthetic data only."""
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from dotenv import dotenv_values


def main():
    config = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    owner = int(config["ALLOWED_TELEGRAM_IDS"].split(",")[0])
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": owner, "first_name": "Owner"})}
    key = hmac.new(b"WebAppData", config["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    created, generated_folders = set(), set()
    marker = "CHEST_AI_SMOKE_" + secrets.token_hex(4)
    with httpx.Client(base_url=config["PUBLIC_URL"] + "/api", headers={"Authorization": "tma " + urlencode(fields)}, timeout=180) as client:
        def request(method, path, **kwargs):
            result = client.request(method, path, **kwargs)
            if result.is_error:
                raise ValueError(f"Smoke {method} {path}: HTTP {result.status_code}")
            return result.json()
        previous_folders = {f["id"] for f in request("GET", "/folders")}
        try:
            models = request("GET", "/ai-settings")
            public_config = models["external"]
            assert "api_key" not in public_config and public_config["api_key_set"]
            draft = {k: v for k, v in public_config.items() if k not in {"api_key_set", "configured", "embedding_dimensions"}}
            draft.update(api_key=None, clear_api_key=False)
            saved = request("PUT", "/ai-settings/external", json=draft)
            assert saved["api_key_set"] and saved["revision"] > public_config["revision"] and "api_key" not in saved
            for task in ["chat", "organize", "embeddings"]:
                check = request("POST", "/ai-settings/external/test", json={"task": task})
                if not check["ok"]:
                    raise ValueError("Saved AI settings probe failed: " + task)
            print("Live UI configuration API + encrypted key preservation + chat/intake/embedding probes: OK", flush=True)
            text = f"Проверочная заметка {marker}. Встреча завтра в 17:30. Код проверки {marker}."
            public = request("POST", "/intake", json={"text": text})
            created.add(public["id"])
            for _ in range(45):
                sources = request("GET", f"/entries/{public['id']}/sources")
                source = next(c for c in sources if c["id"] == public["capture_id"])
                if source["status"] == "applied":
                    created.update(source["result_ids"])
                    assert source["original"]["body"] == text
                    break
                if source["status"] in {"failed", "stale", "waiting"}:
                    raise ValueError("Public intake not processed: " + source["status"])
                time.sleep(2)
            else:
                raise TimeoutError("Public intake did not finish")
            entry = request("GET", f"/entries/{public['id']}")
            assert entry["ai_policy"] == "external" and entry["revision"] >= 2
            assert any(marker in request("GET", f"/entries/{identifier}")["body"] for identifier in created)
            print("Live OpenRouter intake + immutable original + generated note: OK", flush=True)
            start = entry["body"].find("17:30")
            if start >= 0:
                proposal = request("POST", f"/entries/{entry['id']}/assist", json={"instruction": "Замени время на 18:45. Верни только 18:45.", "start": start, "end": start + 5, "revision": entry["revision"]})
                result = request("POST", f"/captures/{proposal['id']}/apply")
                assert result["body"] == entry["body"][:start] + proposal["replacement"] + entry["body"][start + 5:]
                assert "18:45" in result["body"]
                print("Live selected-span LLM edit + exact unchanged surrounding text: OK", flush=True)
            private_text = "Synthetic private routing check " + marker
            private = request("POST", "/intake", json={"text": private_text, "ai_policy": "trusted"})
            created.add(private["id"])
            if not request("GET", "/me")["integrations"]["trusted_llm"]:
                for _ in range(30):
                    source = request("GET", f"/entries/{private['id']}/sources")[0]
                    if source["status"] == "waiting":
                        break
                    time.sleep(1)
                assert source["status"] == "waiting" and source["original"]["body"] == private_text
            external = request("POST", "/search", json={"query": marker, "answer": False, "ai_policy": "external"})
            assert private["id"] not in {row["id"] for row in external["results"]}
            local = request("POST", "/search", json={"query": marker, "ai_policy": "local"})
            assert private["id"] in {row["id"] for row in local["results"]}
            print("Live trusted-unconfigured preservation + external RAG exclusion + local search: OK", flush=True)
        finally:
            for identifier in created:
                response = client.get(f"/entries/{identifier}")
                if response.status_code == 200:
                    folder_id = response.json()["folder_id"]
                    if folder_id and folder_id not in previous_folders:
                        generated_folders.add(folder_id)
                    request("DELETE", f"/entries/{identifier}")
            # Clean only empty, newly generated test folders, never folders in use.
            for identifier in generated_folders:
                contents = request("GET", "/entries", params={"folder_id": identifier})
                folders = request("GET", "/folders")
                if not contents["total"] and not any(f["parent_id"] == identifier for f in folders):
                    request("DELETE", f"/folders/{identifier}")
            print("Synthetic test notes and their source history removed", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Live ingestion check failed: " + type(exc).__name__)
        raise SystemExit(1)
