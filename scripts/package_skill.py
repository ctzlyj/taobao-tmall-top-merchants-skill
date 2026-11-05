#!/usr/bin/env python3
"""构建干净的 SkillHub 发布包：排除缓存、运行产物、个人状态文件与个人路径。"""

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path


SKILL_DIR_NAME = "taobao-tmall-top-merchants"

EXCLUDED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".venv",
    "node_modules",
    "work",
    "outputs",
    "docs",
    "assets",
}
EXCLUDED_FILE_NAMES = {".DS_Store", "Thumbs.db", "subjects.json", ".env"}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".log", ".xlsx", ".xls", ".png", ".jpg", ".jpeg",
    ".pdf", ".zip", ".tmp", ".etl",
}
PORT_PATTERN = re.compile(r"(localhost|127\.0\.0\.1):\d+")


def personal_path_markers():
    # 运行时拼接，避免本文件自身命中扫描。
    user = "caotong" + ".888"
    miniconda = "Miniconda" + "3"
    return [
        user,
        "D:\\" + user,
        "D:/" + user,
        "C:\\Users\\" + user,
        "C:/Users/" + user,
        "D:\\" + miniconda,
        "D:/" + miniconda,
    ]


def collect_files(source_root):
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise ValueError(f"NOT_A_DIRECTORY: {root}")
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if any(part in EXCLUDED_DIRS for part in parts[:-1]):
            continue
        if parts[-1] in EXCLUDED_FILE_NAMES:
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(relative)
    return files


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def find_personal_paths(files, source_root):
    root = Path(source_root).resolve()
    markers = personal_path_markers()
    violations = []
    for relative in files:
        path = root / relative
        text = _read_text(path)
        if text is None:
            continue
        for marker in markers:
            if marker in text:
                violations.append({"file": relative.as_posix(), "marker": marker})
        if PORT_PATTERN.search(text):
            violations.append({"file": relative.as_posix(), "marker": "fixed_localhost_port"})
    return violations


def required_files():
    return {
        Path("SKILL.md"),
        Path("README.md"),
        Path("requirements.txt"),
        Path("scripts/bootstrap.ps1"),
        Path("scripts/preflight.py"),
        Path("scripts/package_skill.py"),
    }


def validate_package(files, source_root):
    problems = []
    relative_set = set(files)
    for required in required_files():
        if required not in relative_set:
            problems.append(f"missing_required:{required.as_posix()}")
    for relative in files:
        parts = relative.parts
        if "__pycache__" in parts or ".pytest_cache" in parts:
            problems.append(f"cache_dir:{relative.as_posix()}")
        if relative.suffix.lower() in {".pyc", ".pyo"}:
            problems.append(f"bytecode:{relative.as_posix()}")
    for violation in find_personal_paths(files, source_root):
        problems.append(f"personal_path:{violation['file']}:{violation['marker']}")
    return problems


def build_staging(source_root, staging_root):
    root = Path(source_root).resolve()
    staging = Path(staging_root).resolve() / SKILL_DIR_NAME
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    copied = []
    for relative in collect_files(root):
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
        copied.append(relative)
    return staging, copied


def build_zip(staging_dir, zip_path):
    staging = Path(staging_dir).resolve()
    archive = Path(zip_path).resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(SKILL_DIR_NAME) / path.relative_to(staging))
    return archive


def main():
    parser = argparse.ArgumentParser(description="构建排除缓存与个人路径的 SkillHub 发布包")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="skillhub-package")
    parser.add_argument("--zip", dest="zip_path", default="")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    files = collect_files(source)
    problems = validate_package(files, source)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    staging, copied = build_staging(source, args.output)
    summary = {
        "ok": True,
        "source": str(source),
        "staging": str(staging),
        "files": len(copied),
        "bytes": sum((staging / item).stat().st_size for item in copied),
    }
    if args.zip_path:
        archive = build_zip(staging, args.zip_path)
        summary["zip"] = str(archive)
        summary["zip_bytes"] = archive.stat().st_size
        summary["zip_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
