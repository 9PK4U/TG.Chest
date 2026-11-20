"""Bound failed local scratch space; never touches cloud backups or live data."""
import argparse
import re
import shutil
from pathlib import Path


def cleanup(root: Path, after_success=False):
    root = root.resolve()
    candidates = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                         and re.fullmatch(r"chest-\d{8}T\d{6}Z-[0-9a-f]{8}", p.name)), key=lambda p: p.name)
    ready = [p for p in candidates if (p / "manifest.json").is_file() and (p / "snapshot.tar.gz").is_file()]
    keep = None if after_success else (ready[-1] if ready else candidates[-1] if candidates else None)
    for path in candidates:
        if path == keep:
            continue
        if path.resolve().parent != root:
            raise ValueError("Invalid scratch cleanup path")
        shutil.rmtree(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--after-success", action="store_true")
    args = parser.parse_args()
    cleanup(args.root, args.after_success)
