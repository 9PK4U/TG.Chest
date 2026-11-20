"""Private, verified snapshots on Disk; safe retention and attachment protection."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path

import httpx

from .config import get_settings

FORMAT = "tg.chest.backup.v1"
PART_SIZE = 8 * 1024 * 1024
NAME = re.compile(r"^chest-\d{8}T\d{6}Z-[0-9a-f]{8}$")
LOG = logging.getLogger(__name__)


class BackupProtected(Exception):
    """A remote attachment still belongs to an in-progress or retained snapshot."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expired_backups(records: list[dict], now: float, days: int = 7) -> list[dict]:
    """The newest complete backup survives even after a long outage."""
    if not records:
        return []
    newest = max(records, key=lambda x: (x["created_at"], x["name"]))
    return [r for r in records if r["name"] != newest["name"] and r["created_at"] < now - days * 86400]


class DiskBackups:
    def __init__(self):
        self.settings = get_settings()
        self.root = self.settings.yandex_disk_root.rstrip("/") + "/backups"
        self.client = httpx.Client(timeout=httpx.Timeout(600, connect=30), follow_redirects=True)
        if not self.settings.yandex_disk_token:
            raise ValueError("YANDEX_DISK_TOKEN is required for backups")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def api(self, method, endpoint="resources", **kwargs):
        for attempt in range(4):
            try:
                response = self.client.request(method, "https://cloud-api.yandex.net/v1/disk/" + endpoint,
                    headers={"Authorization": "OAuth " + self.settings.yandex_disk_token}, **kwargs)
                if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                    return response
            except httpx.TransportError:
                if attempt == 3:
                    raise
            time.sleep(2 ** attempt)

    def metadata(self, path):
        response = self.api("GET", params={"path": path, "fields": "path,size,sha256,type"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def mkdir(self, path):
        prefix, tail = path.split(":/", 1)
        current = prefix + ":/"
        for part in filter(None, tail.split("/")):
            current = current.rstrip("/") + "/" + part
            response = self.api("PUT", params={"path": current})
            if response.status_code != 409:
                response.raise_for_status()

    def put(self, path: str, source: Path):
        digest = sha256(source)
        for attempt in range(3):
            try:
                link = self.api("GET", "resources/upload", params={"path": path, "overwrite": "true"})
                link.raise_for_status()
                with source.open("rb") as file:
                    response = self.client.put(link.json()["href"], content=iter(lambda: file.read(1024 * 1024), b""),
                                               headers={"Content-Length": str(source.stat().st_size)})
                response.raise_for_status()
                info = self.metadata(path)
                if not info or info.get("size") != source.stat().st_size or info.get("sha256") != digest:
                    raise ValueError("Uploaded backup checksum does not match")
                return
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def put_json(self, path: str, data: dict):
        # A real file lets put() verify size and SHA-256 just like large archives.
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "metadata.json"
            source.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.put(path, source)

    def get_json(self, path: str):
        link = self.api("GET", "resources/download", params={"path": path})
        if link.status_code == 404:
            return None
        link.raise_for_status()
        response = self.client.get(link.json()["href"])
        response.raise_for_status()
        return response.json()

    def download(self, path: str, destination: Path):
        info = self.metadata(path)
        if not info:
            raise ValueError("Backup file not found")
        link = self.api("GET", "resources/download", params={"path": path})
        link.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        try:
            with self.client.stream("GET", link.json()["href"]) as response:
                response.raise_for_status()
                with temporary.open("wb") as file:
                    for block in response.iter_bytes(1024 * 1024):
                        file.write(block)
            if temporary.stat().st_size != info["size"] or sha256(temporary) != info["sha256"]:
                raise ValueError("Downloaded backup checksum does not match")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self, path):
        response = self.api("DELETE", params={"path": path, "permanently": "true"})
        if response.status_code != 404:
            response.raise_for_status()
        if response.status_code == 202:
            link = response.json()["href"]
            for _ in range(120):
                response = self.client.get(link, headers={"Authorization": "OAuth " + self.settings.yandex_disk_token})
                response.raise_for_status()
                status = response.json()["status"]
                if status == "success":
                    return
                if status == "failed":
                    raise ValueError("Disk cleanup operation failed")
                time.sleep(1)
            raise TimeoutError("Disk cleanup did not complete")

    def directories(self):
        result, offset = [], 0
        while True:
            response = self.api("GET", params={"path": self.root, "limit": 1000, "offset": offset})
            if response.status_code == 404:
                return []
            response.raise_for_status()
            embedded = response.json().get("_embedded", {})
            batch = embedded.get("items", [])
            result.extend(x["name"] for x in batch if x.get("type") == "dir" and NAME.fullmatch(x["name"]))
            offset += len(batch)
            if not batch or offset >= embedded.get("total", offset):
                return result

    def complete(self):
        result = []
        for name in self.directories():
            record = self.get_json(self.root + "/" + name + "/COMPLETED.json")
            if record is None:
                continue
            # Unexpected metadata must fail closed, never permit unsafe file deletion.
            if record.get("format") != FORMAT or record.get("name") != name or not isinstance(record.get("created_at"), (int, float)):
                raise ValueError("Invalid backup completion metadata")
            if not isinstance(record.get("disk_files"), list) or not all(isinstance(p, str) for p in record["disk_files"]):
                raise ValueError("Invalid protected file list")
            result.append(record)
        return result

    def begin(self, name):
        if not NAME.fullmatch(name):
            raise ValueError("Invalid backup name")
        self.mkdir(self.root)
        self.put_json(self.root + "/IN-PROGRESS.json", {"format": FORMAT, "name": name, "created_at": time.time()})

    def attachment_protected(self, path):
        # Keep deletion fenced if a backup was interrupted; the next successful run clears it.
        if self.metadata(self.root + "/IN-PROGRESS.json") is not None:
            return True
        return any(path in record["disk_files"] for record in self.complete())

    def publish(self, directory: Path):
        manifest_file = directory / "manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        name = manifest["name"]
        if manifest.get("format") != FORMAT or not NAME.fullmatch(name):
            raise ValueError("Invalid manifest")
        archive = directory / "snapshot.tar.gz"
        if sha256(archive) != manifest["archive_sha256"] or archive.stat().st_size != manifest["archive_size"]:
            raise ValueError("Local snapshot checksum mismatch")
        self.mkdir(self.root + "/" + name)
        parts = manifest.get("parts", [])
        if parts:
            if sum(part["size"] for part in parts) != manifest["archive_size"]:
                raise ValueError("Invalid snapshot parts")
            for part in parts:
                if not re.fullmatch(r"snapshot\.part-\d{4}", part["name"]) or sha256(directory / part["name"]) != part["sha256"]:
                    raise ValueError("Corrupt snapshot part")
            # Each worker has its own client; bound memory and request concurrency.
            def upload(part):
                with DiskBackups() as client:
                    client.put(self.root + "/" + name + "/" + part["name"], directory / part["name"])
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(upload, parts))
        else:
            self.put(self.root + "/" + name + "/snapshot.tar.gz", archive)
        for filename in ["manifest.json", "README-RESTORE.md", "restore.py"]:
            self.put(self.root + "/" + name + "/" + filename, directory / filename)
        for filename in ["README-RESTORE.md", "restore.py"]:
            self.put(self.root + "/" + filename, directory / filename)
        completion = {"format": FORMAT, "name": name, "created_at": manifest["created_at"],
                      "archive_sha256": manifest["archive_sha256"], "archive_size": manifest["archive_size"],
                      "manifest_sha256": sha256(manifest_file), "disk_files": manifest["disk_files"]}
        # Written last: an interrupted upload is never considered a successful backup.
        self.put_json(self.root + "/" + name + "/COMPLETED.json", completion)
        records = self.complete()
        if not any(r["name"] == name and r["archive_sha256"] == completion["archive_sha256"] for r in records):
            raise ValueError("Published backup is not verifiable")
        removed = 0
        for record in expired_backups(records, time.time(), self.settings.backup_retention_days):
            self.delete(self.root + "/" + record["name"])
            removed += 1
        complete_names = {r["name"] for r in records}
        for partial in self.directories():
            created = datetime.strptime(partial[6:22], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
            if partial not in complete_names and partial != name and created < time.time() - self.settings.backup_retention_days * 86400:
                self.delete(self.root + "/" + partial)
        self.delete(self.root + "/IN-PROGRESS.json")
        print(json.dumps({"backup": name, "status": "verified", "pruned": removed, "size": archive.stat().st_size}))


def capture_inventory(destination: Path):
    from sqlalchemy import func, select, text
    from .db import SessionLocal
    from .models import AIConfiguration, Attachment, Capture, Chunk, Entry, Folder, Job, User
    with SessionLocal() as db:
        s = get_settings()
        attachments = db.scalars(select(Attachment)).all()
        inventory = {"format": FORMAT, "created_at": time.time(),
            "counts": {model.__tablename__: db.scalar(select(func.count()).select_from(model)) for model in [User, Folder, Entry, Attachment, Chunk, Job, Capture, AIConfiguration]},
            "alembic_revision": db.execute(text("SELECT version_num FROM alembic_version")).scalar(),
            "disk_files": sorted({a.storage_path or f"{s.yandex_disk_root.rstrip('/')}/{a.id}.enc" for a in attachments if a.storage == "yandex"})}
        destination.write_text(json.dumps(inventory, indent=2), encoding="utf-8")


def manifest(directory: Path):
    inventory = json.loads((directory / "inventory.json").read_text(encoding="utf-8"))
    archive = directory / "snapshot.tar.gz"
    result = {**inventory, "name": (directory / "backup-name").read_text().strip(), "archive_sha256": sha256(archive), "archive_size": archive.stat().st_size,
              "scope": "data-only", "postgres_major": 17,
              "members": {p.name: {"sha256": sha256(p), "size": p.stat().st_size}
                          for p in (directory / "payload").iterdir() if p.is_file()}}
    (directory / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def split_snapshot(directory: Path):
    source = directory / "snapshot.tar.gz"
    result = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if sha256(source) != result["archive_sha256"]:
        raise ValueError("Snapshot checksum mismatch")
    if source.stat().st_size <= PART_SIZE:
        return
    parts = []
    with source.open("rb") as file:
        while block := file.read(PART_SIZE):
            name = f"snapshot.part-{len(parts) + 1:04d}"
            (directory / name).write_bytes(block)
            parts.append({"name": name, "size": len(block), "sha256": hashlib.sha256(block).hexdigest()})
    result["parts"] = parts
    (directory / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["begin", "inventory", "manifest", "split", "publish", "list", "fetch"])
    parser.add_argument("target", nargs="?")
    args = parser.parse_args()
    if args.action == "inventory":
        capture_inventory(Path(args.target))
    elif args.action == "manifest":
        manifest(Path(args.target))
        split_snapshot(Path(args.target))
    elif args.action == "split":
        split_snapshot(Path(args.target))
    else:
        with DiskBackups() as disk:
            if args.action == "begin":
                disk.begin(args.target)
            elif args.action == "publish":
                disk.publish(Path(args.target))
            elif args.action == "list":
                print(json.dumps([{k: r[k] for k in ["name", "created_at", "archive_size"]} for r in disk.complete()]))
            elif args.action == "fetch":
                target = Path(args.target)
                if not NAME.fullmatch(target.name):
                    raise ValueError("Destination name must match a backup name")
                target.mkdir(parents=True, exist_ok=True)
                for filename in ["COMPLETED.json", "manifest.json", "restore.py", "README-RESTORE.md"]:
                    disk.download(disk.root + "/" + target.name + "/" + filename, target / filename)
                info = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
                for filename in ([p["name"] for p in info["parts"]] if info.get("parts") else ["snapshot.tar.gz"]):
                    disk.download(disk.root + "/" + target.name + "/" + filename, target / filename)
                print("Verified backup downloaded")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never log signed URLs, credentials, DB contents or provider response bodies.
        status = getattr(getattr(exc, "response", None), "status_code", "n/a")
        print(f"Backup failed: {type(exc).__name__}, HTTP={status}", file=sys.stderr)
        raise SystemExit(1)
