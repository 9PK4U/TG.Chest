"""Download a completed cloud snapshot and restore its dump in an isolated Docker DB.

No live database, container or Docker volume is modified by this check.
"""
import importlib.util
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.backups import DiskBackups, sha256


def command(args, **kwargs):
    return subprocess.run(["docker", *args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=0, help="Wait this many seconds for the first completed cloud backup")
    args = parser.parse_args()
    with DiskBackups() as disk:
        deadline = time.monotonic() + args.wait
        records = disk.complete()
        if not records and args.wait:
            print("Waiting for the first verified cloud snapshot", flush=True)
        while not records and time.monotonic() < deadline:
            time.sleep(10)
            records = disk.complete()
        if not records:
            raise ValueError("No complete cloud backup exists")
        latest = max(records, key=lambda r: (r["created_at"], r["name"]))
        folder = ROOT / "data" / "recovery-check" / latest["name"]
        folder.mkdir(parents=True, exist_ok=True)
        for filename in ["COMPLETED.json", "manifest.json", "restore.py", "README-RESTORE.md"]:
            remote = disk.root + "/" + latest["name"] + "/" + filename
            destination = folder / filename
            info = disk.metadata(remote)
            if destination.is_file() and info and destination.stat().st_size == info["size"] and sha256(destination) == info["sha256"]:
                continue
            disk.download(remote, destination)
        info = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        filenames = [p["name"] for p in info["parts"]] if info.get("parts") else ["snapshot.tar.gz"]
        # Fetch every cloud part for the recovery drill, even when a local archive is cached.
        def fetch_part(filename):
            with DiskBackups() as source:
                remote = source.root + "/" + latest["name"] + "/" + filename
                destination = folder / filename
                stored = source.metadata(remote)
                if destination.is_file() and stored and destination.stat().st_size == stored["size"] and sha256(destination) == stored["sha256"]:
                    return
                source.download(remote, destination)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(fetch_part, filenames))
        if info.get("parts"):
            (folder / "snapshot.tar.gz").unlink(missing_ok=True)
    spec = importlib.util.spec_from_file_location("snapshot_restore", folder / "restore.py")
    restore = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restore)
    extracted = folder / "verified"
    extracted.mkdir(exist_ok=True)
    manifest = restore.verify(folder, extracted)
    print("Cloud backup and every archive member: SHA-256 verified", flush=True)
    database_image = "pgvector/pgvector:pg17"
    container = "tg-chest-restore-check-" + secrets.token_hex(4)
    created = False
    try:
        command(["run", "-d", "--name", container, "--network", "none", "-e", "POSTGRES_PASSWORD=" + secrets.token_hex(16),
                 "-e", "POSTGRES_DB=restore_check", database_image], stdout=subprocess.DEVNULL)
        created = True
        for _ in range(60):
            if subprocess.run(["docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "restore_check"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(1)
        else:
            raise TimeoutError("Recovery test database did not start")
        with (extracted / "database.dump").open("rb") as dump:
            command(["exec", "-i", container, "pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "-U", "postgres", "-d", "restore_check"], stdin=dump)
        allowed_tables = {"users", "folders", "entries", "attachments", "chunks", "jobs", "captures", "ai_configurations"}
        tables = list(manifest["counts"])
        if not set(tables) <= allowed_tables:
            raise ValueError("Unknown inventory tables")
        query = "SELECT json_build_object(" + ",".join(f"'{t}',(SELECT count(*) FROM {t})" for t in tables) + ")::text;"
        output = command(["exec", container, "psql", "-U", "postgres", "-d", "restore_check", "-At", "-c", query], capture_output=True)
        counts = json.loads(output.stdout)
        if counts != manifest["counts"]:
            raise ValueError("Recovered record counts differ from the snapshot inventory")
        output = command(["exec", container, "psql", "-U", "postgres", "-d", "restore_check", "-At", "-c",
                          "SELECT extname FROM pg_extension WHERE extname='vector';"], capture_output=True)
        if output.stdout.strip() != b"vector":
            raise ValueError("pgvector extension was not recovered")
        # Recovering settings verifies that the data remains decryptable after server loss.
        import tarfile
        project = extracted / "project"
        project.mkdir(exist_ok=True)
        with tarfile.open(extracted / "settings.tar.gz") as archive:
            archive.extractall(project, filter="data")
        from dotenv import dotenv_values
        config = dotenv_values(project / ".env")
        for key in ["FILE_ENCRYPTION_KEY", "YANDEX_DISK_TOKEN", "BOT_TOKEN", "OPENROUTER_API_KEY", "POSTGRES_PASSWORD"]:
            if not config.get(key):
                raise ValueError("Recovered server configuration lacks required credentials")
        if manifest["counts"].get("ai_configurations", 0):
            from cryptography.fernet import Fernet
            output = command(["exec", container, "psql", "-U", "postgres", "-d", "restore_check", "-At", "-c",
                              "SELECT payload FROM ai_configurations;"], capture_output=True)
            encryption = Fernet(config["FILE_ENCRYPTION_KEY"].encode())
            for encrypted in output.stdout.splitlines():
                recovered = json.loads(encryption.decrypt(encrypted))
                if not isinstance(recovered, dict) or "api_key" not in recovered:
                    raise ValueError("Recovered model configuration is invalid")
            print("Encrypted live model configuration decrypted after restore: OK (values hidden)", flush=True)
        client = json.loads((project / "yandex-client.secrets.json").read_text(encoding="utf-8"))
        if not client.get("client_id") or not client.get("client_secret"):
            raise ValueError("OAuth application credentials were not recovered")
        print("Isolated PostgreSQL restore + pgvector + all table counts: OK", flush=True)
        print("Data-only scope and recovery credentials: OK (values hidden)", flush=True)
        print("Backup checked:", latest["name"], flush=True)
    finally:
        if created:
            command(["rm", "-f", "-v", container], stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Recovery check failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
