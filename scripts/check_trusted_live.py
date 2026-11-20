"""Synthetic end-to-end check of the saved trusted provider, without external fallback."""
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
    created = set()
    marker = "CHEST_TRUSTED_" + secrets.token_hex(4)
    with httpx.Client(base_url=config["PUBLIC_URL"] + "/api", headers={"Authorization": "tma " + urlencode(fields)}, timeout=180) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise ValueError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()
        original_folders = {f["id"] for f in request("GET", "/folders")}
        own_folders = set()
        try:
            for task in ["chat", "organize"]:
                result = request("POST", "/ai-settings/trusted/test", json={"task": task})
                if not result["ok"]:
                    raise ValueError("Trusted probe failed: " + task)
            print("Trusted saved configuration: chat + structured tests OK", flush=True)
            text = f"Synthetic private test {marker}. Meeting at 15:00. Keep the reference {marker}."
            note = request("POST", "/intake", json={"text": text, "ai_policy": "trusted"})
            created.add(note["id"])
            for _ in range(75):
                records = request("GET", f"/entries/{note['id']}/sources")
                record = next(c for c in records if c["id"] == note["capture_id"])
                if record["status"] == "applied":
                    created.update(record["result_ids"])
                    assert record["original"]["body"] == text
                    break
                if record["status"] in {"waiting", "failed", "stale"}:
                    raise ValueError("Trusted intake status: " + record["status"])
                time.sleep(2)
            else:
                raise TimeoutError("Trusted intake did not finish")
            entries = [request("GET", f"/entries/{identifier}") for identifier in created]
            assert all(entry["ai_policy"] == "trusted" for entry in entries)
            print("Trusted worker intake + encrypted original: OK", flush=True)
            entry = next(e for e in entries if "15:00" in e["body"])
            start = entry["body"].index("15:00")
            proposal = request("POST", f"/entries/{entry['id']}/assist", json={"instruction": "Replace the selected time with 16:00. Return only 16:00.", "start": start, "end": start + 5, "revision": entry["revision"]})
            edited = request("POST", f"/captures/{proposal['id']}/apply")
            assert "16:00" in edited["body"]
            assert edited["body"] == entry["body"][:start] + proposal["replacement"] + entry["body"][start + 5:]
            print("Trusted selected-span edit, surrounding text unchanged: OK", flush=True)
            for _ in range(15):
                state = request("GET", f"/entries/{edited['id']}")
                if state["index_status"] not in {"pending", "failed"}:
                    break
                time.sleep(1)
            result = request("POST", "/search", json={"query": marker, "ai_policy": "trusted"})
            assert any(row["id"] in created for row in result["results"])
            assert result["answer"]
            print("Trusted search + generated answer: OK", flush=True)
            external = request("POST", "/search", json={"query": marker, "ai_policy": "external", "answer": False})
            assert not any(row["id"] in created for row in external["results"])
            print("Private test records excluded from external search: OK", flush=True)
        finally:
            for identifier in created:
                response = client.get(f"/entries/{identifier}")
                if response.status_code == 200:
                    folder = response.json()["folder_id"]
                    if folder and folder not in original_folders:
                        own_folders.add(folder)
                    request("DELETE", f"/entries/{identifier}")
            # Only remove empty newly assigned folders; never touch a folder in use.
            for identifier in own_folders:
                current = request("GET", "/folders")
                if not request("GET", "/entries", params={"folder_id": identifier})["total"] and not any(f["parent_id"] == identifier for f in current):
                    request("DELETE", f"/folders/{identifier}")
            print("Synthetic trusted test data cleaned up", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Trusted smoke failed:", type(exc).__name__)
        raise SystemExit(1)
