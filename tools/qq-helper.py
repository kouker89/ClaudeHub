"""QQ Bridge helper — process queue/results + task/score tracking without loading full JSON into context."""
import json
import os
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
# When EXE writes data elsewhere (BRIDGE_DATA_DIR), use that for queue/results
BRIDGE_DIR = os.environ.get("BRIDGE_DATA_DIR", "")
if BRIDGE_DIR:
    _qq_dir = BRIDGE_DIR
else:
    local_dir = os.path.join(ROOT, "session-context", "qq-bridge")
    HUB_FALLBACK = r"D:\claude-hub\session-context\qq-bridge"
    if os.path.isdir(HUB_FALLBACK) and os.path.exists(os.path.join(HUB_FALLBACK, "consumer.log")):
        _qq_dir = HUB_FALLBACK
    else:
        _qq_dir = local_dir
QUEUE_PATH = os.path.join(_qq_dir, "claude-queue.json")
RESULTS_PATH = os.path.join(_qq_dir, "claude-results.json")
ARCHIVE_PATH = os.path.join(_qq_dir, "claude-queue-archive.json")

def _session_index():
    """Get the session index for this Claude Code instance from env."""
    try:
        return int(os.environ.get("CLAUDESESSION_SESSION_INDEX", "0"))
    except ValueError:
        return 0

def _all_queue_files():
    """List session queue files for this Claude Code instance."""
    import glob
    si = _session_index()
    if si == 0:
        return [os.path.join(_qq_dir, "claude-queue.json")]
    return [os.path.join(_qq_dir, f"claude-queue-{si}.json")]


def _all_results_files():
    """List session results files for this Claude Code instance."""
    si = _session_index()
    if si == 0:
        return [os.path.join(_qq_dir, "claude-results.json")]
    return [os.path.join(_qq_dir, f"claude-results-{si}.json")]
def _task_path():
    si = _session_index()
    if si == 0:
        return os.path.join(ROOT, "session-context", "active-task.json")
    return os.path.join(ROOT, "session-context", f"active-task-{si}.json")

def _score_path():
    si = _session_index()
    if si == 0:
        return os.path.join(ROOT, "session-context", "proposal-score.json")
    return os.path.join(ROOT, "session-context", f"proposal-score-{si}.json")

def _query_path():
    si = _session_index()
    if si == 0:
        return os.path.join(_qq_dir, "kunkun-queries.json")
    return os.path.join(_qq_dir, f"bot-queries-{si}.json")
CONFIG_PATH = os.path.join(_qq_dir, "config.json")
AUTH_PIN_PATH = os.path.join(ROOT, "session-context", "auth-pin.json")
MAX_RESULTS = 30


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cmd_pending():
    """Print pending queue items from all session queues."""
    all_pending = []
    for qf in _all_queue_files():
        queue = load_json(qf)
        all_pending.extend(item for item in queue if item.get("status") == "pending")
    if all_pending:
        print(json.dumps(all_pending, ensure_ascii=False, indent=2))
    else:
        print("[]")


def cmd_done(item_id):
    """Mark a queue item as done (searches all session queues)."""
    for qf in _all_queue_files():
        queue = load_json(qf)
        for item in queue:
            if item.get("id") == item_id:
                item["status"] = "done"
                save_json(qf, queue)
                print(f"Marked {item_id} as done.")
                return
    print(f"Item {item_id} not found.")


def cmd_result(item_id, msg_id, *result_parts):
    """Write result + mark queue done. Searches all session queues/files."""
    result_text = " ".join(result_parts)

    # Find queue item across all session queues
    user_id = ""
    bot_id = ""
    found_qf = None
    for qf in _all_queue_files():
        queue = load_json(qf)
        for item in queue:
            if item.get("id") == item_id:
                user_id = item.get("user_id", "")
                bot_id = item.get("bot_id", "")
                found_qf = qf
                # Mark done
                item["status"] = "done"
                save_json(qf, queue)
                break
        if found_qf:
            break

    if not found_qf:
        print(f"Item {item_id} not found in any queue.")
        return

    # Write to corresponding results file (match session index)
    # claude-queue-1.json -> claude-results-1.json
    rpath = RESULTS_PATH  # default
    if "claude-queue-" in found_qf:
        # Extract session index from filename
        import re
        m = re.search(r'claude-queue-(\d+)\.json', found_qf)
        if m:
            rpath = os.path.join(_qq_dir, f"claude-results-{m.group(1)}.json")

    results = load_json(rpath) if os.path.exists(rpath) else []
    results.insert(0, {
        "id": item_id,
        "user_id": user_id,
        "msg_id": msg_id,
        "bot_id": bot_id,
        "result": result_text,
        "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    })
    save_json(rpath, results)
    print(f"Result + done: {item_id}")


def cmd_trim():
    """Trim all results to MAX_RESULTS, archive old queue items (>50 done)."""
    for rf in _all_results_files():
        results = load_json(rf)
        if len(results) > MAX_RESULTS:
            save_json(rf, results[:MAX_RESULTS])
            print(f"Results trimmed ({os.path.basename(rf)}): {len(results)} → {MAX_RESULTS}")

    archive = load_json(ARCHIVE_PATH) if os.path.exists(ARCHIVE_PATH) else []
    for qf in _all_queue_files():
        queue = load_json(qf)
        kept = []
        moved = 0
        for item in queue:
            if item.get("status") == "done" and len(kept) > 30:
                archive.append(item)
                moved += 1
            else:
                kept.append(item)
        save_json(qf, kept)
        if moved:
            save_json(ARCHIVE_PATH, archive)
            tag = os.path.basename(qf)
            print(f"Queue trimmed ({tag}): {len(queue)} → {len(kept)} ({moved} archived)")


def cmd_status():
    """Quick status: pending count, queue size, results size + task + score."""
    total_queue = 0
    total_pending = 0
    for qf in _all_queue_files():
        queue = load_json(qf)
        total_queue += len(queue)
        total_pending += sum(1 for item in queue if item.get("status") == "pending")
    total_results = 0
    for rf in _all_results_files():
        total_results += len(load_json(rf))
    task_info = ""
    if os.path.exists(_task_path()):
        t = load_json(_task_path())
        cur = t.get("current")
        if cur:
            pct = round(cur["steps_done"] / cur["steps_total"] * 100) if cur["steps_total"] > 0 else 0
            task_info = f" | Task: {cur['name']} [{pct}%] ({cur['steps_done']}/{cur['steps_total']})"
    score_info = ""
    if os.path.exists(_score_path()):
        s = load_json(_score_path())
        score_info = f" | Score: {s.get('daily_score', 0)} (tasks:{s.get('today_tasks', 0)} err:{s.get('today_mistakes', 0)})"
    print(f"Pending: {total_pending} | Queue: {total_queue} items | Results: {total_results} items{task_info}{score_info}")


# ── Task lifecycle ──

def cmd_task_start(name, total_steps):
    """Start a new task with estimated steps."""
    data = {}
    if os.path.exists(_task_path()):
        data = load_json(_task_path())
    data["current"] = {
        "name": name,
        "steps_done": 0,
        "steps_total": int(total_steps),
        "started": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }
    data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_json(_task_path(), data)
    print(f"Task started: {name} (0/{total_steps})")


def cmd_task_step():
    """Increment current task steps_done by 1."""
    if not os.path.exists(_task_path()):
        print("No active task.")
        return
    data = load_json(_task_path())
    cur = data.get("current")
    if not cur:
        print("No active task.")
        return
    cur["steps_done"] = min(cur["steps_done"] + 1, cur["steps_total"])
    data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_json(_task_path(), data)
    pct = round(cur["steps_done"] / cur["steps_total"] * 100)
    print(f"Task step: {cur['name']} [{pct}%] ({cur['steps_done']}/{cur['steps_total']})")


def cmd_task_done():
    """Mark current task as done, increment done_today counter."""
    if not os.path.exists(_task_path()):
        print("No active task.")
        return
    data = load_json(_task_path())
    cur = data.get("current")
    if not cur:
        print("No active task.")
        return
    cur["steps_done"] = cur["steps_total"]
    data["done_today"] = data.get("done_today", 0) + 1
    data["current"] = None
    data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_json(_task_path(), data)
    print(f"Task done: {cur['name']} | Done today: {data['done_today']}")


def cmd_task_busy(*name_parts):
    """Set current task (no steps needed). Shows in terminal status."""
    name = " ".join(name_parts) if name_parts else "工作中"
    data = {}
    if os.path.exists(_task_path()):
        data = load_json(_task_path())
    data["current"] = {
        "name": name,
        "steps_done": 0,
        "steps_total": 0,
        "started": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }
    data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_json(_task_path(), data)
    print(f"Busy: {name}")


def cmd_task_idle():
    """Clear current task. Terminal shows idle."""
    data = {}
    if os.path.exists(_task_path()):
        data = load_json(_task_path())
    cur = data.get("current")
    if cur:
        data["done_today"] = data.get("done_today", 0) + 1
    data["current"] = None
    data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_json(_task_path(), data)
    print("Idle")


# ── Score tracking ──

def cmd_score_add(amount, *reason_parts):
    """Add/subtract points. amount can be positive or negative."""
    amount = int(amount)
    reason = " ".join(reason_parts) if reason_parts else ""
    today = datetime.now().strftime("%Y-%m-%d")

    data = {"lifetime_score": 0, "daily_score": 0, "today_tasks": 0, "today_mistakes": 0, "history": []}
    if os.path.exists(_score_path()):
        existing = load_json(_score_path())
        data["lifetime_score"] = existing.get("lifetime_score", 0)
        data["history"] = existing.get("history", [])
        # Reset daily if new day
        old_date = existing.get("date", "")
        if old_date == today:
            data["daily_score"] = existing.get("daily_score", 0)
            data["today_tasks"] = existing.get("today_tasks", 0)
            data["today_mistakes"] = existing.get("today_mistakes", 0)

    data["date"] = today
    data["daily_score"] += amount
    data["lifetime_score"] += amount
    if amount < 0:
        data["today_mistakes"] += 1

    data["history"].append({
        "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "amount": amount,
        "reason": reason
    })
    # Keep only last 50 history entries
    if len(data["history"]) > 50:
        data["history"] = data["history"][-50:]

    save_json(_score_path(), data)
    sign = "+" if amount >= 0 else ""
    print(f"Score {sign}{amount} | Daily: {data['daily_score']} | Lifetime: {data['lifetime_score']}")


def cmd_task_done_scored():
    """Mark task done AND add +1 score for successful completion."""
    cmd_task_done()
    cmd_score_add(1, "Task completed")


# ── Kunkun queries ──

def cmd_check_queries():
    """List pending kunkun queries that need answers."""
    if not os.path.exists(_query_path()):
        print("[]")
        return
    queries = load_json(_query_path())
    pending = [q for q in queries if q.get("status") == "pending"]
    if pending:
        print(json.dumps(pending, ensure_ascii=False, indent=2))
    else:
        print("[]")


def cmd_answer_query(query_id, *answer_parts):
    """Answer a kunkun query via HTTP to bridge, mark as answered."""
    answer_text = " ".join(answer_parts)
    if not answer_text:
        print("Error: answer text required")
        return

    # Read queries to get user_id/msg_id (may have been filled in later)
    queries = []
    if os.path.exists(_query_path()):
        queries = load_json(_query_path())

    query = None
    for q in queries:
        if q.get("id") == query_id:
            query = q
            break

    if not query:
        print(f"Query {query_id} not found")
        return

    # Send answer via bridge HTTP endpoint
    cfg = load_json(CONFIG_PATH)
    bridge_token = cfg.get("bridge_token", "")
    import urllib.request
    body_data = {
        "user_id": query.get("user_id", "0D5531D06F4FC10669A8A70B92423827"),
        "msg_id": query.get("msg_id", ""),
        "text": answer_text
    }
    bot_id = query.get("bot_id", "")
    if bot_id:
        body_data["bot_id"] = bot_id
    body = json.dumps(body_data).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:9876/reply",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bridge_token}"
        },
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        result = resp.read().decode()
        if "200" in str(result) or resp.status == 200:
            # Mark as answered
            query["status"] = "answered"
            query["answer"] = answer_text
            save_json(_query_path(), queries)
            print(f"Answered query {query_id}: {answer_text[:50]}")
        else:
            print(f"Bridge returned: {result}")
    except Exception as e:
        print(f"Failed to send answer: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/qq-helper.py [pending|done|result|trim|status|task-start|task-step|task-done|score-add|task-done-scored|check-queries|answer-query]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "pending":
        cmd_pending()
    elif cmd == "done":
        cmd_done(sys.argv[2])
    elif cmd == "result":
        if len(sys.argv) < 5:
            print("Usage: python tools/qq-helper.py result <id> <msg_id> <text...>")
            sys.exit(1)
        cmd_result(sys.argv[2], sys.argv[3], *sys.argv[4:])
    elif cmd == "trim":
        cmd_trim()
    elif cmd == "status":
        cmd_status()
    elif cmd == "task-start":
        if len(sys.argv) < 4:
            print("Usage: python tools/qq-helper.py task-start <name> <total_steps>")
            sys.exit(1)
        cmd_task_start(sys.argv[2], sys.argv[3])
    elif cmd == "task-step":
        cmd_task_step()
    elif cmd == "task-done":
        cmd_task_done()
    elif cmd == "task-busy":
        cmd_task_busy(*sys.argv[2:])
    elif cmd == "task-idle":
        cmd_task_idle()
    elif cmd == "task-idle-scored":
        cmd_task_idle()
        cmd_score_add(1, "Task completed")
    elif cmd == "task-done-scored":
        cmd_task_done_scored()
    elif cmd == "score-add":
        if len(sys.argv) < 3:
            print("Usage: python tools/qq-helper.py score-add <amount> [reason...]")
            sys.exit(1)
        cmd_score_add(sys.argv[2], *sys.argv[3:])
    elif cmd == "check-queries":
        cmd_check_queries()
    elif cmd == "answer-query":
        if len(sys.argv) < 4:
            print("Usage: python tools/qq-helper.py answer-query <query_id> <answer_text...>")
            sys.exit(1)
        cmd_answer_query(sys.argv[2], *sys.argv[3:])
    elif cmd == "check-pin":
        if len(sys.argv) < 3:
            print("Usage: python tools/qq-helper.py check-pin <pin>")
            sys.exit(1)
        pin_data = load_json(AUTH_PIN_PATH) if os.path.exists(AUTH_PIN_PATH) else {}
        if sys.argv[2] == pin_data.get("pin", ""):
            print("OK")
        else:
            print("FAIL")
            sys.exit(1)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
