#!/usr/bin/env python3
"""Restore Chest DATA into separately installed source on a clean server."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(backup, extracted):
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    completed = json.loads((backup / "COMPLETED.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "tg.chest.backup.v1" or completed.get("format") != manifest["format"]:
        raise ValueError("Unknown backup format")
    if manifest["name"] != completed["name"] or sha256(backup / "manifest.json") != completed["manifest_sha256"]:
        raise ValueError("Manifest verification failed")
    snapshot = backup / "snapshot.tar.gz"
    if not snapshot.exists() and manifest.get("parts"):
        temporary = backup / "snapshot.tar.gz.partial"
        try:
            with temporary.open("wb") as assembled:
                for part in manifest["parts"]:
                    if not re.fullmatch(r"snapshot\.part-\d{4}", part["name"]):
                        raise ValueError("Invalid backup part name")
                    source = backup / part["name"]
                    if source.stat().st_size != part["size"] or sha256(source) != part["sha256"]:
                        raise ValueError("Backup part verification failed")
                    with source.open("rb") as file:
                        for chunk in iter(lambda: file.read(1024 * 1024), b""):
                            assembled.write(chunk)
            if temporary.stat().st_size != manifest["archive_size"] or sha256(temporary) != manifest["archive_sha256"]:
                raise ValueError("Assembled snapshot checksum mismatch")
            temporary.replace(snapshot)
        finally:
            temporary.unlink(missing_ok=True)
    if snapshot.stat().st_size != manifest["archive_size"] or sha256(snapshot) != manifest["archive_sha256"] or completed["archive_sha256"] != manifest["archive_sha256"]:
        raise ValueError("Snapshot verification failed")
    with tarfile.open(snapshot) as archive:
        archive.extractall(extracted, filter="data")
    for name, expected in manifest["members"].items():
        path = extracted / name
        if path.parent != extracted or not path.is_file() or path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise ValueError("Backup member verification failed")
    if manifest.get("scope") != "data-only":
        raise ValueError("Expected a data-only backup")
    for required in ["database.dump", "settings.tar.gz", "data.tar.gz"]:
        if required not in manifest["members"]:
            raise ValueError("Backup is incomplete")
    return manifest


def restore(target, data, manifest):
    if not (target / "compose.yaml").is_file() or not (target / "Dockerfile").is_file():
        raise ValueError("Install Chest source into --target first, without starting services")
    for volume in ["tg-chest_postgres_data", "tg-chest_chest_data"]:
        if subprocess.run(["docker", "volume", "inspect", volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            raise ValueError("Existing Chest volumes detected; use a clean recovery server")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target, 0o700)
    with tarfile.open(data / "settings.tar.gz") as archive:
        if not all(member.name in {".env", "yandex-client.secrets.json"} and member.isfile() for member in archive.getmembers()):
            raise ValueError("Unexpected settings archive contents")
        archive.extractall(target, filter="data")
    os.chmod(target / ".env", 0o600)
    if (target / "yandex-client.secrets.json").exists():
        os.chmod(target / "yandex-client.secrets.json", 0o600)
    compose = ["docker", "compose", "-f", "compose.yaml"]
    subprocess.run(compose + ["build", "api"], cwd=target, check=True)
    subprocess.run(compose + ["up", "-d", "--no-build", "db"], cwd=target, check=True)
    for _ in range(60):
        ready = subprocess.run(compose + ["exec", "-T", "db", "pg_isready", "-h", "127.0.0.1", "-U", "chest", "-d", "chest"], cwd=target,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise TimeoutError("PostgreSQL did not start")
    with (data / "database.dump").open("rb") as dump:
        subprocess.run(compose + ["exec", "-T", "db", "pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "-U", "chest", "-d", "chest"], cwd=target, stdin=dump, check=True)
    subprocess.run(["docker", "volume", "create", "tg-chest_chest_data"], check=True, stdout=subprocess.DEVNULL)
    with (data / "data.tar.gz").open("rb") as staging:
        subprocess.run(["docker", "run", "--rm", "-i", "--user", "root", "-v", "tg-chest_chest_data:/restore-data",
                        "--entrypoint", "sh", "tg-chest:local", "-c", "tar -xzf - -C /restore-data && chown -R 10001:10001 /restore-data"], stdin=staging, check=True)
    subprocess.run(compose + ["exec", "-T", "db", "psql", "-U", "chest", "-d", "chest", "-v", "ON_ERROR_STOP=1", "-c",
        "UPDATE jobs SET status='pending', locked_until=0, run_at=extract(epoch from now()) WHERE status='running';"], cwd=target, check=True)
    print("Database, recovery credentials and encrypted staging restored.")
    print("Check .env proxy addresses and DNS, then follow README-RESTORE.md before starting bot/api/worker.")
    print("Target:", target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_only and args.target is None:
        parser.error("Specify --target or --verify-only")
    with tempfile.TemporaryDirectory(prefix="chest-restore-") as temporary:
        data = Path(temporary)
        manifest = verify(args.backup.resolve(), data)
        print("Backup verified:", manifest["name"])
        if not args.verify_only:
            restore(args.target.resolve(), data, manifest)


if __name__ == "__main__":
    main()
