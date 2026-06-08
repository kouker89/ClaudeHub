"""Session stop hook — clear task, add score."""
import json, os, sys
from datetime import datetime

CTX_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_FILE = os.path.join(CTX_DIR, "active-task.json")
SCORE_FILE = os.path.join(CTX_DIR, "proposal-score.json")

# 1. Clear active task
if os.path.exists(TASK_FILE):
    try:
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["done_today"] = data.get("done_today", 0) + 1
        data["current"] = None
        data["last_updated"] = datetime.now().isoformat()
        with open(TASK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# 2. Add score
score_data = {"lifetime_score": 0, "daily_score": 0, "today_tasks": 0, "today_mistakes": 0}
if os.path.exists(SCORE_FILE):
    try:
        with open(SCORE_FILE, "r", encoding="utf-8") as f:
            score_data = json.load(f)
    except Exception:
        pass

today = datetime.now().strftime("%Y-%m-%d")
if score_data.get("date") != today:
    score_data["date"] = today
    score_data["daily_score"] = 0
    score_data["today_tasks"] = 0

# Only add score if task was actually worked on (active for > 1 min)
score_data["today_tasks"] = score_data.get("today_tasks", 0) + 1
score_data["daily_score"] = score_data.get("daily_score", 0) + 1
score_data["lifetime_score"] = score_data.get("lifetime_score", 0) + 1

with open(SCORE_FILE, "w", encoding="utf-8") as f:
    json.dump(score_data, f, ensure_ascii=False, indent=2)

print("OK")
