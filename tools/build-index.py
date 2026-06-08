"""Build project file index for fast lookup. Run: python tools/build-index.py"""
import json, os, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(os.path.dirname(__file__), "file-index.json")
IGNORE = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
          "*.pyc", "*.pyo", "*.pyd", ".bak", ".tmp", "*.egg-info", ".claude/worktrees"}

def should_ignore(path, root):
    rel = os.path.relpath(path, root)
    parts = rel.replace("\\", "/").split("/")
    for part in parts:
        if part in IGNORE:
            return True
    name = os.path.basename(path)
    for pat in IGNORE:
        if pat.startswith("*") and name.endswith(pat[1:]):
            return True
    return False

def build_index():
    index = {}
    file_count = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Filter ignored dirs
        dirnames[:] = [d for d in dirnames if not should_ignore(d, ROOT)]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if should_ignore(full, ROOT):
                continue
            rel = os.path.relpath(full, ROOT)
            size = os.path.getsize(full)
            mtime = os.path.getmtime(full)
            # Index by filename (case-insensitive)
            key = fname.lower()
            if key not in index:
                index[key] = []
            index[key].append({
                "path": rel,
                "size": size,
                "mtime": mtime
            })
            file_count += 1

    result = {
        "root": ROOT,
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "file_count": file_count,
        "index": index
    }
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"Indexed {file_count} files → {INDEX_PATH}")

if __name__ == "__main__":
    build_index()
