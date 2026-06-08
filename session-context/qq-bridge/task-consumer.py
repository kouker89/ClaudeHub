"""
Task relay — forwards QQ tasks to Claude Code, relays results back to QQ.
"""
import ctypes, glob, json, os, sys, time, httpx

DIR = os.environ.get("BRIDGE_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(DIR, "task-inbox.json")

# Per-session queue/results files: session 0 uses legacy names for backward compat
def _queue_file(session_idx: int) -> str:
    if session_idx == 0:
        return os.path.join(DIR, "claude-queue.json")
    return os.path.join(DIR, f"claude-queue-{session_idx}.json")

def _results_file(session_idx: int) -> str:
    if session_idx == 0:
        return os.path.join(DIR, "claude-results.json")
    return os.path.join(DIR, f"claude-results-{session_idx}.json")

def _all_queue_files() -> list[str]:
    """Return paths to all existing per-session queue files."""
    files = glob.glob(os.path.join(DIR, "claude-queue*.json"))
    return sorted(files)

def _all_results_files() -> list[str]:
    """Return paths to all existing per-session results files."""
    files = glob.glob(os.path.join(DIR, "claude-results*.json"))
    return sorted(files)

def _load_bot_configs() -> tuple[dict[str, int], dict[int, str]]:
    """Return ({bot_id: session_index}, {session_index: bot_name}) from bridge config."""
    cfg_path = os.path.join(DIR, "config.json")
    bot_map = {}
    name_map = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        for b in cfg.get("bots", []):
            bot_map[b["id"]] = b.get("session_index", 0)
            name_map[b.get("session_index", 0)] = b.get("name", b["id"])
    return bot_map, name_map

BOT_SESSION_MAP: dict[str, int] = {}
SESSION_BOT_NAME: dict[int, str] = {}
BUDGET_FILE = os.path.join(DIR, "token-budget.json")
WATCHDOG_FILE = os.path.join(DIR, "watchdog.hb")
PID_FILE = os.path.join(DIR, "consumer.pid")
BRIDGE_URL = "http://127.0.0.1:9876/reply"
POLL_INTERVAL = 2
STALE_MINUTES = 5
MAX_RETRIES = 3
WATCHDOG_TIMEOUT = 300

def _pid_alive(pid):
    """Check if a Windows process is still running."""
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
    if h:
        kernel32.CloseHandle(h)
        return True
    return False

def _acquire_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            if _pid_alive(old_pid):
                return False
        except (ValueError, OSError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def _release_lock():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass

# Budget tracking
def _load_budget():
    if os.path.exists(BUDGET_FILE):
        with open(BUDGET_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"daily_limit": 1.00, "used_today": 0, "date": "", "paused": False, "estimate_per_task": 0.02}

def _save_budget(b):
    write_json(BUDGET_FILE, b)

def check_budget() -> bool:
    return True

def spend_budget(user_id: str, content: str):
    return (True, None)

def _load_bridge_token():
    cfg_path = os.path.join(DIR, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f).get("bridge_token", "")
    return ""

BRIDGE_TOKEN = _load_bridge_token()


LOG_MAX = 100 * 1024  # 100KB

def log(msg):
    t = time.strftime("%H:%M:%S")
    try:
        print(f"[relay {t}] {msg}", flush=True)
    except UnicodeEncodeError:
        safe = str(msg).encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace')
        print(f"[relay {t}] {safe}", flush=True)
    try:
        log_path = os.path.join(DIR, "consumer.log")
        if os.path.exists(log_path) and os.path.getsize(log_path) > LOG_MAX:
            old = log_path + ".old"
            try:
                os.replace(log_path, old)
            except OSError:
                pass
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{t}] {msg}\n")
    except:
        pass


def read_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def write_json(path, data):
    bak = path + ".bak"
    if os.path.exists(path):
        try:
            os.replace(path, bak)
        except OSError:
            pass
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(bak):
            try:
                os.replace(bak, path)
            except OSError:
                pass
        raise


def send_with_retry(user_id: str, text: str, msg_id: str = "", bot_id: str = "") -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        headers = {}
        if BRIDGE_TOKEN:
            headers["Authorization"] = f"Bearer {BRIDGE_TOKEN}"
        body = {"user_id": user_id, "msg_id": msg_id, "text": text}
        if bot_id:
            body["bot_id"] = bot_id
        try:
            r = httpx.post(BRIDGE_URL, json=body, headers=headers, timeout=10)
            if r.status_code == 200:
                log(f"reply sent -> {user_id}")
                return True
            else:
                log(f"reply failed [{r.status_code}] attempt {attempt}/{MAX_RETRIES}: {r.text[:100]}")
        except Exception as e:
            log(f"reply error attempt {attempt}/{MAX_RETRIES}: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(attempt * 2)
    log(f"reply FAILED after {MAX_RETRIES} retries -> {user_id}")
    return False


CACHE_FILE = os.path.join(DIR, "claude-cache.json")
CACHE_TTL = 300  # 5 minutes
CACHE_MAX = 50
MERGE_WINDOW = 3  # seconds — merge same-user messages within this window

def _load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return []

def _save_cache(c):
    try:
        with open(CACHE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(CACHE_FILE + ".tmp", CACHE_FILE)
    except Exception:
        pass

def _normalize_content(content):
    """Normalize for cache key comparison."""
    return " ".join(content.lower().strip().split())

def _check_cache(user_id, content):
    """Return cached result if found and not expired, else None."""
    cache = _load_cache()
    norm = _normalize_content(content)
    now = time.time()
    for entry in cache:
        if entry.get("user_id") == user_id and entry.get("content_hash") == norm:
            cached_at = entry.get("cached_at", 0)
            if isinstance(cached_at, str):
                try:
                    cached_at = time.mktime(time.strptime(cached_at, "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    cached_at = 0
            if now - cached_at < CACHE_TTL:
                return entry.get("result")
    return None

def _update_cache(user_id, content, result):
    """Store result in cache, evict old entries if needed."""
    cache = _load_cache()
    norm = _normalize_content(content)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    # Remove existing entry for same user+content
    cache = [e for e in cache if not (e.get("user_id") == user_id and e.get("content_hash") == norm)]
    cache.append({"user_id": user_id, "content_hash": norm, "result": result, "cached_at": now})
    if len(cache) > CACHE_MAX:
        cache = cache[-CACHE_MAX:]
    _save_cache(cache)

def process_inbox():
    """Forward new pending tasks from inbox to per-session Claude Code queues.
    Routes by bot_id -> session_index so each session only sees its own tasks."""
    items = read_json(INBOX)

    # Per-session queues: {session_idx: [tasks]}
    session_queues: dict[int, list] = {}
    session_seen: dict[int, set] = {}
    # Load existing per-session queues
    for session_idx in set(BOT_SESSION_MAP.values()):
        session_queues[session_idx] = read_json(_queue_file(session_idx))
        session_seen[session_idx] = {q["msg_id"] for q in session_queues[session_idx]}
    # Also handle session 0 if no bots mapped
    if 0 not in session_queues:
        session_queues[0] = read_json(_queue_file(0))
        session_seen[0] = {q["msg_id"] for q in session_queues[0]}

    changed = False
    budget_warned = False

    # Group pending items by user
    from collections import OrderedDict
    user_pending = OrderedDict()
    for item in items:
        if item.get("status") != "pending":
            continue
        content = item.get("content")
        if not content:
            continue
        msg_id = item.get("msg_id", "")
        # Check if msg_id already seen in any session
        if any(msg_id in seen for seen in session_seen.values()):
            continue
        uid = item["user_id"]
        if uid not in user_pending:
            user_pending[uid] = []
        user_pending[uid].append(item)

    for uid, uitems in user_pending.items():
        if not uitems:
            continue

        uitems.sort(key=lambda x: x.get("time", ""))

        # Merge items within MERGE_WINDOW seconds
        windows = []
        current_window = [uitems[0]]
        for item in uitems[1:]:
            try:
                first_ts = time.mktime(time.strptime(current_window[0].get("time", ""), "%Y-%m-%dT%H:%M:%S"))
                cur_ts = time.mktime(time.strptime(item.get("time", ""), "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, OverflowError):
                current_window.append(item)
                continue
            if cur_ts - first_ts <= MERGE_WINDOW:
                current_window.append(item)
            else:
                windows.append(current_window)
                current_window = [item]
        windows.append(current_window)

        for window in windows:
            if not window:
                continue

            # Determine session from first item's bot_id
            bot_id = window[0].get("bot_id", "bot-1")
            session_idx = BOT_SESSION_MAP.get(bot_id, 0)
            if session_idx not in session_queues:
                session_queues[session_idx] = read_json(_queue_file(session_idx))
                session_seen[session_idx] = {q["msg_id"] for q in session_queues[session_idx]}

            if len(window) == 1:
                item = window[0]
                content = item["content"]
                msg_id = item.get("msg_id", "")

                # Check cache
                cached = _check_cache(uid, content)
                if cached is not None:
                    log(f"cache hit: {content[:40]}")
                    results = read_json(_results_file(session_idx))
                    results.append({
                        "id": item["id"],
                        "user_id": uid,
                        "msg_id": msg_id,
                        "bot_id": bot_id,
                        "result": f"[缓存复用] {cached}",
                        "time": time.strftime("%Y-%m-%dT%H:%M:%S")
                    })
                    write_json(_results_file(session_idx), results)
                    item["status"] = "forwarded"
                    session_seen[session_idx].add(msg_id)
                    changed = True
                    continue

                ok, warning = spend_budget(uid, content)
                if not ok:
                    log(f"budget exceeded, task skipped: {content[:40]}")
                    send_with_retry(uid, "Token日预算已达上限，明天0点自动重置。", msg_id, bot_id)
                    item["status"] = "forwarded"
                    changed = True
                    continue

                if warning and not budget_warned:
                    send_with_retry(uid, warning, "", bot_id)
                    budget_warned = True

                task = {
                    "id": item["id"],
                    "user_id": uid,
                    "msg_id": msg_id,
                    "bot_id": bot_id,
                    "content": content,
                    "time": item.get("time", ""),
                    "status": "pending"
                }
                session_queues[session_idx].append(task)
                session_seen[session_idx].add(msg_id)
                log(f"[{SESSION_BOT_NAME.get(session_idx, f's{session_idx}')}] queued for Claude: {content[:60]}")

                item["status"] = "forwarded"
                changed = True
            else:
                merged_content = "【合并消息 — 请综合回复以下所有内容，只回复一次】\n\n" + \
                    "\n\n---\n\n".join([f"消息{i+1}: {it['content']}" for i, it in enumerate(window)])
                merged_msg_ids = [it.get("msg_id", "") for it in window]
                combined_id = window[0]["id"]

                ok, warning = spend_budget(uid, merged_content)
                if not ok:
                    log(f"budget exceeded, merged task skipped ({len(window)} msgs)")
                    for it in window:
                        send_with_retry(uid, "Token日预算已达上限，明天0点自动重置。", it.get("msg_id", ""), bot_id)
                        it["status"] = "forwarded"
                    changed = True
                    continue

                if warning and not budget_warned:
                    send_with_retry(uid, warning, "", bot_id)
                    budget_warned = True

                task = {
                    "id": combined_id,
                    "user_id": uid,
                    "msg_id": ",".join(merged_msg_ids),
                    "bot_id": bot_id,
                    "content": merged_content,
                    "time": window[0].get("time", ""),
                    "status": "pending"
                }
                session_queues[session_idx].append(task)
                for mid in merged_msg_ids:
                    session_seen[session_idx].add(mid)
                log(f"[{SESSION_BOT_NAME.get(session_idx, f's{session_idx}')}] merged {len(window)} msgs queued for Claude")

                for it in window:
                    it["status"] = "forwarded"
                changed = True

    if changed:
        write_json(INBOX, items)
        for session_idx, queue in session_queues.items():
            write_json(_queue_file(session_idx), queue)


def process_results():
    """Send completed results back to QQ. Checks all per-session results files."""
    changed = False
    sent_msg_ids = set()

    for rfile in _all_results_files():
        results = read_json(rfile)
        file_changed = False

        for r in results:
            if not isinstance(r, dict):
                continue
            if r.get("status") == "done":
                if r.get("sent_time"):
                    sent_msg_ids.add(r.get("msg_id", ""))
                continue
            if r.get("sent_time"):
                sent_msg_ids.add(r.get("msg_id", ""))
                continue

            msg_id = r.get("msg_id", "")
            if msg_id and msg_id in sent_msg_ids:
                r["status"] = "done"
                r["sent_time"] = r.get("sent_time") or time.strftime("%Y-%m-%dT%H:%M:%S")
                file_changed = True
                log(f"dup result skipped: {r.get('id', '?')}")
                continue

            single_msg_id = msg_id.split(",")[0].strip() if msg_id else ""
            send_with_retry(r["user_id"],
                r.get('result', '(无输出)'),
                single_msg_id,
                r.get("bot_id", ""))
            r["status"] = "done"
            r["sent_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if msg_id:
                sent_msg_ids.add(msg_id)
            file_changed = True
            log(f"result sent for {r.get('id', '?')}")

        if file_changed:
            write_json(rfile, results)
            changed = True


def process_permission_requests():
    """Notify QQ when a PreToolUse hook writes a permission request."""
    req_file = os.path.join(DIR, "permission-request.json")
    if not os.path.exists(req_file):
        return
    try:
        requests = read_json(req_file)
    except Exception:
        return
    changed = False
    for r in requests:
        if r.get("status") == "pending" and not r.get("notified"):
            send_with_retry(
                r.get("user_id", "0D5531D06F4FC10669A8A70B92423827"),
                f"Claude Code 需要权限确认:\n\n{r.get('tool_desc', '未知工具')}\n\n回复 Y 允许 / N 拒绝\n(5分钟超时自动拒绝)",
                r.get("msg_id", ""),
                r.get("bot_id", ""))
            r["notified"] = True
            changed = True
            log(f"permission request sent for {r.get('id', '?')}: {r.get('tool_desc', '')[:60]}")
    if changed:
        write_json(req_file, requests)


def stale_task_check():
    """Detect pending tasks older than STALE_MINUTES and notify user."""
    # Collect all answered msg_ids across all results files
    answered_ids: set[str] = set()
    for rfile in _all_results_files():
        results = read_json(rfile)
        answered_ids.update(r.get("msg_id", "") for r in results if r.get("msg_id"))

    now = time.time()

    for qfile in _all_queue_files():
        queue = read_json(qfile)
        changed = False

        for task in queue:
            if task.get("status") != "pending":
                continue
            task_time = task.get("time", "")
            if not task_time:
                continue
            try:
                ts = time.mktime(time.strptime(task_time, "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue

            if task.get("msg_id") in answered_ids:
                log(f"stale but answered: {task['content'][:60]}")
                task["status"] = "done"
                changed = True
                continue

            if now - ts > STALE_MINUTES * 60:
                log(f"stale task detected: {task['content'][:60]}")
                send_with_retry(task["user_id"],
                    "处理超时，你的任务暂时没有完成。请稍后重试或重新发送。",
                    task.get("msg_id", ""),
                    task.get("bot_id", ""))
                task["status"] = "timeout"
                changed = True

        if changed:
            write_json(qfile, queue)


def update_watchdog():
    """Touch watchdog heartbeat file so Claude Code can see consumer is alive."""
    try:
        with open(WATCHDOG_FILE, "w") as f:
            f.write(str(int(time.time())))
    except:
        pass


def startup_check():
    total_pending = 0
    for qfile in _all_queue_files():
        queue = read_json(qfile)
        total_pending += sum(1 for t in queue if t.get("status") == "pending")
    inbox_pending = sum(1 for t in read_json(INBOX) if t.get("status") == "pending")
    if total_pending or inbox_pending:
        log(f"startup: {total_pending} pending in queues, {inbox_pending} in inbox")
    else:
        log("startup: no backlog")


def main():
    if not _acquire_lock():
        log("another consumer is already running, exiting")
        sys.exit(0)
    try:
        while True:
            try:
                _main_loop()
            except Exception:
                import traceback
                log(f"main loop crashed, restarting in 3s...\n{traceback.format_exc()}")
                time.sleep(3)
            # If _main_loop returns (shouldn't happen), restart
            log("main loop exited unexpectedly, restarting in 3s...")
            time.sleep(3)
    finally:
        _release_lock()

def _main_loop():
    global BOT_SESSION_MAP, SESSION_BOT_NAME
    BOT_SESSION_MAP, SESSION_BOT_NAME = _load_bot_configs()
    log("task relay started (Claude Code mode)")
    startup_check()

    stale_counter = 0
    wd_counter = 0
    map_refresh_counter = 0
    while True:
        try:
            process_inbox()
            process_results()
            process_permission_requests()

            stale_counter += 1
            if stale_counter >= 15:
                stale_task_check()
                stale_counter = 0

            wd_counter += 1
            if wd_counter >= 30:
                update_watchdog()
                wd_counter = 0

            map_refresh_counter += 1
            if map_refresh_counter >= 60:
                BOT_SESSION_MAP, SESSION_BOT_NAME = _load_bot_configs()
                map_refresh_counter = 0
        except Exception as e:
            import traceback
            log(f"error: {e}\n{traceback.format_exc()}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
