"""Session start hook — mark bot as busy. Idempotent: skips if updated < 5 min ago."""
import json, os, sys
from datetime import datetime, timedelta

CTX_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_FILE = os.path.join(CTX_DIR, "active-task.json")
UPDATE_FILE = os.path.join(CTX_DIR, ".last_start_hook")

# Debounce: only run once per 5 minutes
now = datetime.now()
if os.path.exists(UPDATE_FILE):
    try:
        with open(UPDATE_FILE, "r") as f:
            last = datetime.fromisoformat(f.read().strip())
        if (now - last) < timedelta(minutes=5):
            sys.exit(0)  # Skip, already ran recently
    except Exception:
        pass

# Mark as busy
data = {"current": None, "done_today": 0, "last_updated": now.isoformat()}
if os.path.exists(TASK_FILE):
    try:
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass

if data.get("current") is None:
    data["current"] = {
        "name": "工作中",
        "steps_done": 0,
        "steps_total": 1,
        "started": now.isoformat()
    }
data["last_updated"] = now.isoformat()

with open(TASK_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

# Timestamp for debounce
with open(UPDATE_FILE, "w") as f:
    f.write(now.isoformat())

print("OK")
