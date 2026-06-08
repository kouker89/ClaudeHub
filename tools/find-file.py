"""Fast file search using pre-built index. Run: python tools/find-file.py --name <file>"""
import json
import os
import sys
import argparse
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(SCRIPT_DIR, "file-index.json")
ROOT = os.path.dirname(SCRIPT_DIR)


def load_index():
    if not os.path.exists(INDEX_PATH):
        print("[!] Index not found. Run: python tools/build-index.py")
        return None
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def search_by_name(index, name):
    key = name.lower()
    entries = index.get("index", {}).get(key, [])
    if not entries:
        # Try partial match
        results = []
        for k, v in index.get("index", {}).items():
            if key in k:
                results.extend(v)
        return results
    return entries


def search_by_keyword(index, keyword):
    results = []
    kw = keyword.lower()
    for entries in index.get("index", {}).values():
        for e in entries:
            if kw in e["path"].lower():
                results.append(e)
    return results


def search_by_content(index, text):
    """Fallback to ripgrep for content search."""
    try:
        result = subprocess.run(
            ["rg", "--no-heading", "-l", text, ROOT],
            capture_output=True, text=True, timeout=30
        )
        files = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        return [{"path": f, "match": "content"} for f in files]
    except FileNotFoundError:
        # rg not available, use python fallback for small files
        results = []
        for entries in index.get("index", {}).values():
            for e in entries:
                if e["size"] > 1024 * 1024:  # skip >1MB
                    continue
                try:
                    fpath = os.path.join(ROOT, e["path"])
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        if text.lower() in f.read().lower():
                            results.append(e)
                except Exception:
                    pass
        return results


def print_results(results):
    if not results:
        print("No matches found.")
        return
    print(f"Found {len(results)} file(s):")
    for r in results:
        size_kb = r.get("size", 0) / 1024
        print(f"  {r['path']}  ({size_kb:.1f} KB)")


def main():
    parser = argparse.ArgumentParser(description="Fast file search using pre-built index")
    parser.add_argument("--name", help="Search by filename (hash lookup, instant)")
    parser.add_argument("--keyword", help="Search by keyword in path")
    parser.add_argument("--content", help="Search by file content (slower)")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild index before search")
    args = parser.parse_args()

    if args.rebuild:
        build_script = os.path.join(SCRIPT_DIR, "build-index.py")
        subprocess.run([sys.executable, build_script], cwd=ROOT)

    if not any([args.name, args.keyword, args.content]):
        parser.print_help()
        return

    index = load_index()
    if not index:
        sys.exit(1)

    if args.name:
        results = search_by_name(index, args.name)
    elif args.keyword:
        results = search_by_keyword(index, args.keyword)
    elif args.content:
        results = search_by_content(index, args.content)
    else:
        results = []

    print_results(results)


if __name__ == "__main__":
    main()
