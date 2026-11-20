"""Prepare ignored deployment artifacts. Credentials never enter the source archive."""
import io
import subprocess
import tarfile
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
output = root / "data" / "deploy"
output.mkdir(parents=True, exist_ok=True)
files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).decode().split("\0")
with tarfile.open(output / "source.tgz", "w:gz") as archive:
    for name in files:
        if name and (root / name).is_file():
            archive.add(root / name, arcname=name)
values = dotenv_values(root / ".env")
values.update(ENVIRONMENT="production", DEV_AUTH="false", TELEGRAM_PROXY="http://127.0.0.1:7890", DATA_DIR="/app/data")
with (output / ".env").open("w", encoding="utf-8", newline="\n") as file:
    for key, value in values.items():
        if value is not None:
            file.write(key + "='" + value.replace("'", "\\'") + "'\n")
with tarfile.open(output / "source.tgz") as archive:
    names = archive.getnames()
    assert not any(name in {".env", "openrouter-key.txt", "yandex-client.secrets.json"} for name in names)
print(f"Packaged {len(names)} source files; production credentials stored separately (hidden)")
