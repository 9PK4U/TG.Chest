import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.backups import BackupProtected, DiskBackups, FORMAT, expired_backups, manifest, sha256, split_snapshot
from app.models import Job
from app.services import enqueue
from app.worker import claim_job, run_job


def record(name, created, files=None):
    return {"format": FORMAT, "name": name, "created_at": created, "disk_files": files or [],
            "archive_sha256": "old-hash", "archive_size": 1, "manifest_sha256": "old-manifest"}


def test_retention_keeps_latest_during_long_outage():
    old = record("chest-20260101T000000Z-00000001", 1)
    latest = record("chest-20260102T000000Z-00000002", 2)
    assert expired_backups([old, latest], 100 * 86400) == [old]
    assert expired_backups([latest], 100 * 86400) == []
    assert expired_backups([], 100 * 86400) == []


def test_retention_keeps_entire_week_and_only_removes_older():
    now = 100 * 86400
    records = [record(str(days), now - days * 86400) for days in range(10)]
    assert [r["name"] for r in expired_backups(records, now)] == ["8", "9"]


class MemoryDisk(DiskBackups):
    def __init__(self):
        self.root = "app:/TG.Chest/backups"
        self.settings = SimpleNamespace(backup_retention_days=7)
        self.files, self.dirs, self.deleted = {}, set(), []
        self.fail_archive = False

    def __exit__(self, *args):
        pass

    def mkdir(self, path):
        if path != self.root:
            self.dirs.add(path.rsplit("/", 1)[-1])

    def put(self, path, source):
        if self.fail_archive and path.endswith("snapshot.tar.gz"):
            raise RuntimeError("simulated upload failure")
        self.files[path] = source.read_bytes()

    def put_json(self, path, data):
        self.files[path] = json.dumps(data).encode()

    def get_json(self, path):
        return json.loads(self.files[path]) if path in self.files else None

    def metadata(self, path):
        return {"exists": True} if path in self.files else None

    def directories(self):
        return sorted(self.dirs)

    def delete(self, path):
        self.deleted.append(path)
        self.files = {p: v for p, v in self.files.items() if p != path and not p.startswith(path + "/")}
        self.dirs.discard(path.rsplit("/", 1)[-1])


@pytest.fixture
def snapshot(tmp_path):
    import tarfile
    directory = tmp_path / "chest-20261021T120000Z-1234abcd"
    payload = directory / "payload"
    payload.mkdir(parents=True)
    for name in ["database.dump", "settings.tar.gz", "data.tar.gz"]:
        (payload / name).write_bytes(b"private test data")
    (directory / "backup-name").write_text(directory.name)
    (directory / "inventory.json").write_text(json.dumps({"format": FORMAT, "created_at": 2_000_000_000,
        "counts": {"entries": 4}, "disk_files": ["app:/TG.Chest/protected.enc"]}))
    for filename in ["README-RESTORE.md", "restore.py"]:
        (directory / filename).write_text("recovery instructions")
    with tarfile.open(directory / "snapshot.tar.gz", "w:gz") as archive:
        for file in payload.iterdir():
            archive.add(file, arcname=file.name)
    manifest(directory)
    return directory


def test_failed_publish_never_prunes_or_creates_completion(snapshot):
    disk = MemoryDisk()
    old = record("chest-20260101T000000Z-00000001", 1)
    disk.dirs.add(old["name"])
    disk.put_json(disk.root + "/" + old["name"] + "/COMPLETED.json", old)
    disk.begin(snapshot.name)
    disk.fail_archive = True
    with pytest.raises(RuntimeError):
        disk.publish(snapshot)
    assert disk.deleted == []
    assert not disk.get_json(disk.root + "/" + snapshot.name + "/COMPLETED.json")
    assert disk.attachment_protected("any file")


def test_verified_publish_prunes_old_and_protects_referenced_files(snapshot):
    disk = MemoryDisk()
    old = record("chest-20260101T000000Z-00000001", 1)
    disk.dirs.add(old["name"])
    disk.put_json(disk.root + "/" + old["name"] + "/COMPLETED.json", old)
    disk.begin(snapshot.name)
    disk.publish(snapshot)
    assert disk.root + "/" + old["name"] in disk.deleted
    assert disk.root + "/" + snapshot.name not in disk.deleted
    assert disk.metadata(disk.root + "/IN-PROGRESS.json") is None
    assert disk.attachment_protected("app:/TG.Chest/protected.enc")
    assert not disk.attachment_protected("app:/TG.Chest/unreferenced.enc")
    assert disk.root + "/README-RESTORE.md" in disk.files
    assert disk.root + "/restore.py" in disk.files


def test_restore_verifies_all_members_and_rejects_corruption(snapshot, tmp_path):
    disk = MemoryDisk()
    disk.publish(snapshot)
    completion = disk.get_json(disk.root + "/" + snapshot.name + "/COMPLETED.json")
    (snapshot / "COMPLETED.json").write_text(json.dumps(completion))
    path = Path(__file__).resolve().parents[2] / "scripts" / "restore.py"
    spec = importlib.util.spec_from_file_location("restore_backup", path)
    restore = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restore)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    assert restore.verify(snapshot, extracted)["counts"]["entries"] == 4
    info = json.loads((snapshot / "manifest.json").read_text())
    assert info["scope"] == "data-only"
    assert set(info["members"]) == {"database.dump", "settings.tar.gz", "data.tar.gz"}
    assert "images" not in info
    archive = snapshot / "snapshot.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="Snapshot verification failed"):
        restore.verify(snapshot, extracted)


def test_backup_protected_attachment_defers_without_exhausting_retries(database, monkeypatch):
    async def protected(path):
        raise BackupProtected()
    monkeypatch.setattr("app.worker.delete_disk", protected)
    with database() as db:
        enqueue(db, "delete_disk", "app:/TG.Chest/protected.enc")
        db.commit()
    identifier = claim_job()
    asyncio.run(run_job(identifier))
    with database() as db:
        job = db.get(Job, identifier)
        assert job.status == "pending" and job.attempts == 0 and job.error == ""
        assert job.locked_until == 0


def test_failed_local_work_cannot_fill_the_server_disk(tmp_path):
    path = Path(__file__).resolve().parents[2] / "scripts" / "clean_backup_work.py"
    spec = importlib.util.spec_from_file_location("clean_backup_work", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ready = tmp_path / "chest-20261020T000000Z-00000001"
    ready.mkdir()
    (ready / "manifest.json").write_text("{}")
    (ready / "snapshot.tar.gz").write_bytes(b"last complete local capture")
    for number in range(2, 8):
        (tmp_path / f"chest-20261021T000000Z-{number:08x}").mkdir()
    live = tmp_path / "unrelated-live-data"
    live.mkdir()
    module.cleanup(tmp_path)
    assert ready.exists() and live.exists()
    assert len(list(tmp_path.iterdir())) == 2
    module.cleanup(tmp_path, after_success=True)
    assert list(tmp_path.iterdir()) == [live]


def test_parted_backup_publishes_and_reassembles_with_checksums(snapshot, tmp_path, monkeypatch):
    monkeypatch.setattr("app.backups.PART_SIZE", 100)
    split_snapshot(snapshot)
    info = json.loads((snapshot / "manifest.json").read_text())
    assert len(info["parts"]) > 1
    disk = MemoryDisk()
    monkeypatch.setattr("app.backups.DiskBackups", lambda: disk)
    disk.publish(snapshot)
    assert disk.root + "/" + snapshot.name + "/snapshot.tar.gz" not in disk.files
    completion = disk.get_json(disk.root + "/" + snapshot.name + "/COMPLETED.json")
    (snapshot / "COMPLETED.json").write_text(json.dumps(completion))
    spec = importlib.util.spec_from_file_location("restore_parts", Path(__file__).resolve().parents[2] / "scripts" / "restore.py")
    restore = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restore)
    (snapshot / "snapshot.tar.gz").unlink()
    extracted = tmp_path / "restored-parts"
    extracted.mkdir()
    restore.verify(snapshot, extracted)
    assert sha256(snapshot / "snapshot.tar.gz") == info["archive_sha256"]
    (snapshot / "snapshot.tar.gz").unlink()
    (snapshot / info["parts"][0]["name"]).write_bytes(b"damaged part")
    with pytest.raises(ValueError, match="Backup part verification failed"):
        restore.verify(snapshot, extracted)
