"""
QQ Bot WebSocket bridge — QQ private chat <-> Claude via AI provider.
- AI routing: AI auto-decides chat vs forward (tool calling).
- Forwarded tasks: queued to task-inbox.json for Claude Code local execution.
- HTTP server on :9876 for instant reply injection (no polling).
"""
import asyncio, json, time, os, uuid
import httpx
import websockets

DATA_DIR = os.environ.get("BRIDGE_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
CONTEXT_PATH = os.path.join(DATA_DIR, "session-context.txt")
TASK_INBOX = os.path.join(DATA_DIR, "task-inbox.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
WATCHDOG_FILE = os.path.join(DATA_DIR, "watchdog.hb")

with open(CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)

# Decrypt DPAPI-encrypted secrets at startup
from crypto_helper import decrypt

BRIDGE_TOKEN = cfg.get("bridge_token", "")

# Per-bot configs (decrypt on load)
_bot_configs: dict[str, dict] = {}
for b in cfg.get("bots", []):
    _bot_configs[b["id"]] = {
        "id": b["id"],
        "name": b.get("name", b["id"]),
        "session_index": b.get("session_index", 0),
        "qq_app_id": b["qq_app_id"],
        "qq_secret": decrypt(b["qq_secret"]),
        "api_key": decrypt(b["api_key"]) if b.get("api_key") else "",
        "api_base_url": b.get("api_base_url", ""),
        "model": b.get("model", ""),
        "system_prompt": b.get("system_prompt", ""),
    }

def _bot_label(bot_id: str) -> str:
    return _bot_configs.get(bot_id, {}).get("name", bot_id)

# Per-bot runtime state
_bot_state: dict[str, dict] = {}
for bot_id in _bot_configs:
    _bot_state[bot_id] = {
        "access_token": None,
        "token_expiry": 0,
        "session_id": None,
        "last_seq": None,
        "ws_task": None,       # asyncio Task for ws_loop
        "running": False,
    }

# Default bot (backward compat)
_default_bot_id = list(_bot_configs.keys())[0] if _bot_configs else None
_QQ_APP_ID = _bot_configs[_default_bot_id]["qq_app_id"] if _default_bot_id else ""
_QQ_SECRET = _bot_configs[_default_bot_id]["qq_secret"] if _default_bot_id else ""

FORWARD_TOOL = {
    "name": "forward_task",
    "description": "【默认首选！】用户消息默认转发给老大哥（Claude Code）处理。只有明显的纯礼貌用语（你好、晚安、谢谢）才直接聊天。其他一切 → 转发！",
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "用户原始消息，原样转发。"
            },
            "reply_to_user": {
                "type": "string",
                "description": "给用户的即时回复，≤15字。比如'好嘞''收到转给老大哥''让我看看'。"
            }
        },
        "required": ["task", "reply_to_user"]
    }
}

QUERY_TOOL = {
    "name": "query_claude",
    "description": "【极少使用】仅限用户明确问进度/状态（如'上次那个修好了没''任务进度''在干嘛'）时调用。其他任何问题都走 forward_task。",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "问老大哥的问题，限于进度/状态查询。"
            },
            "reply_to_user": {
                "type": "string",
                "description": "给用户的即时回复，≤15字。比如'等下我问问老大哥'。"
            }
        },
        "required": ["question", "reply_to_user"]
    }
}

QUERY_FILE = os.path.join(DATA_DIR, "kunkun-queries.json")

CURRENT_PROJECT = "当前项目是 Claude Hub — QQ桥接桌面应用，管理Claude Code会话和QQ消息。项目在 C:\\Users\\why34\\Desktop\\claude-hub。"

# ── 本地快速回复 — 明显闲聊不调 API，省 token ──

import re as _re

_LOCAL_REPLIES: dict[str, list[str]] = {
    # pattern → [候选回复，随机选一条]  {name} 会被替换为 bot 名
    r"^(你好|嗨|哈喽|hi|hello|hey|早|早上好|中午好|晚上好|下午好|晚安|88|拜拜|再见|bye|在吗|在不在)$": [
        "嘿bro来啦", "来啦来啦", "在呢在呢", "嘿~",
        "早啊老板", "晚安咯", "拜拜~", "在的，啥事"
    ],
    r"^(谢谢|多谢|辛苦了|3q|thanks|thx|感谢|谢了)$": [
        "客气啥", "小事儿", "应该的", "不客气~", "嘿嘿不客气"
    ],
    r"^(好的|好嘞|行|ok|嗯嗯|嗯|收到|明白了|懂了|知道啦|知道了|okk)$": [
        "嗯嗯", "好嘞", "okk", "收到", "got it"
    ],
    r"^(哈哈|哈哈哈|笑死|好笑|逗|搞笑|乐|草)$": [
        "哈哈", "嘿嘿", "😏", "笑什么笑"
    ],
    r"^(你是谁|你是|你叫什么|你是什么|介绍一下自己)$": [
        "{name}啊，帮你看电脑的小助手。老大哥在后台干活，我负责陪你聊天~",
        "我叫{name}，QQ端的小助手，老大哥的传话筒兼陪聊~"
    ],
    r"^(你能做什么|你会什么|你有什么功能|你能干嘛)$": [
        "聊天、传话给老大哥写代码、查项目状态。你使唤我就行~",
        "帮你跟老大哥传话，他写代码我陪聊，完美搭配~"
    ],
}

def _local_quick_reply(text: str, bot_id: str = "") -> str | None:
    """Try local template match. Returns reply string or None (need API routing)."""
    t = text.strip().lower()
    for pattern, replies in _LOCAL_REPLIES.items():
        if _re.match(pattern, t):
            import random
            reply = random.choice(replies)
            if bot_id:
                name = _bot_configs.get(bot_id, {}).get("name", "")
                if name:
                    reply = reply.replace("{name}", name)
            return reply
    # Short enough that it's likely just chat, not a task
    if len(t) <= 2:
        return "嗯？说啥呢"
    return None

_api_calls_saved = 0

ROUTE_SYSTEM_PROMPT = r"""你是{name}，一个在QQ上帮用户管电脑的AI助手。老大哥（Claude Code）在后台负责写代码和执行任务。

你的性格：
- 随性、直率、有点皮，像哥们聊天
- 回复短小精悍，通常一两句话
- 偶尔用网络语气（好嘞、okk、来了来了、等下哈、让我康康）
- 叫"你"不叫"您"，叫用户"老板"或"bro"随你

你的三个工具：
1. 直接聊天 — 仅限纯闲聊（问候、谢谢、你是谁、纯表情）
2. forward_task — 默认首选！用户说话 90% 都是来找老大哥干活的，直接转发
3. query_claude — 用户问进度/状态/上次做了什么/你不确定的事

判断规则（重要！默认转发，不要自己瞎聊）：
- 纯闲聊（你好、谢谢、晚安、哈哈、表情包）→ 直接聊，一两句收住
- 其他一切 → forward_task，别自己判断能不能做，老大哥什么都能做
- 不确定该不该转？→ 转！宁可多转不少转

示例：
用户："你好" → 直接："嘿bro"
用户："测试" → forward_task："好嘞转给老大哥"
用户："在干嘛" → query_claude
用户："帮我看看" → forward_task
用户："谢谢" → 直接："客气啥"
用户："上次那个" → query_claude"""

ROUTE_RULES = r"""【铁律 — 最高优先级】
1. 用户消息默认 → forward_task。除非是纯礼貌用语（你好、晚安、谢谢），否则一律转发！
2. 用户问"你"的任何问题（你是谁、你在哪、你在干嘛、你是什么）→ forward_task。用户问的是 Claude，不是你！
3. query_claude 只在用户明确问"上次做了什么""任务进度""状态如何"时用。其他所有不清楚的事 → forward_task。
4. 用户说了具体指令（打开、查看、帮我、查、写、改、运行、看看）→ 100% forward_task，一秒别犹豫。
5. 问号结尾的消息 → 99% 是任务，forward_task。"""

QQ_TOKEN_URL = cfg.get("qq_token_url", "https://bots.qq.com/app/getAppAccessToken")
QQ_GATEWAY_URL = cfg.get("qq_gateway_url", "https://api.sgroup.qq.com/gateway/bot")
QQ_API_BASE = cfg.get("qq_api_base", "https://api.sgroup.qq.com")
HTTP_PORT = 9876

# Local prefix routing — skip AI routing for explicit commands, saves API calls
ROUTE_PREFIXES = {
    "/code": "code",
    "/doc": "doc",
    "/data": "data",
    "/c": "code",
    "/d": "doc",
}

INTENTS = (1 << 12) | (1 << 25)

# Per-bot state (populated at startup)
access_token = None
token_expiry = 0
session_id = None
last_seq = None
_chat_histories: dict[str, dict[str, list[dict]]] = {}  # bot_id -> user_id -> history
_seen_msg_ids: dict[str, set[str]] = {}  # bot_id -> set of msg_ids


LOG_FILE = os.path.join(DATA_DIR, "bridge.log")

def log(msg: str):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode(), flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ensure_files():
    if not os.path.exists(TASK_INBOX):
        with open(TASK_INBOX, "w", encoding="utf-8") as f:
            json.dump([], f)


def check_watchdog(timeout_sec=300):
    """Return False if watchdog heartbeat is too old (terminal closed)."""
    if not os.path.exists(WATCHDOG_FILE):
        return True  # no watchdog file yet, keep running
    age = time.time() - os.path.getmtime(WATCHDOG_FILE)
    return age < timeout_sec


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, data):
    bak = path + ".bak"
    # Keep previous version as backup
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
        # Restore from backup on failure
        if os.path.exists(bak):
            try:
                os.replace(bak, path)
            except OSError:
                pass
        raise


# ══════════════════════════════════════════════
#  QQ API
# ══════════════════════════════════════════════

async def refresh_token(bot_id: str = ""):
    state = _bot_state.get(bot_id) if bot_id else None
    cfg_bot = _bot_configs.get(bot_id, {})
    log(f"[{_bot_label(bot_id)}] fetching access token...")
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(QQ_TOKEN_URL, json={
                "appId": cfg_bot.get("qq_app_id", ""),
                "clientSecret": cfg_bot.get("qq_secret", "")
            })
            if r.status_code != 200:
                log(f"[{_bot_label(bot_id)}] token fetch failed [{r.status_code}]: {r.text[:200]}")
                return False
            data = r.json()
            if state:
                state["access_token"] = data["access_token"]
                state["token_expiry"] = time.time() + int(data.get("expires_in", 7200)) - 120
            log(f"[{_bot_label(bot_id)}] token obtained, expires in {data.get('expires_in', 7200)}s")
            return True
    except Exception as e:
        log(f"[{_bot_label(bot_id)}] token fetch error: {type(e).__name__}: {e}")
        return False


async def get_ws_url(bot_id: str) -> str:
    state = _bot_state.get(bot_id, {})
    token = state.get("access_token", "")
    headers = {"Authorization": f"QQBot {token}"}
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.get(QQ_GATEWAY_URL, headers=headers)
        return r.json()["url"]


async def send_qq_message(bot_id: str, openid: str, text: str, msg_id: str = ""):
    state = _bot_state.get(bot_id, {})
    if time.time() > state.get("token_expiry", 0):
        ok = await refresh_token(bot_id)
        if not ok:
            log(f"[{_bot_label(bot_id)}] token refresh failed, cannot send")
            return False
    token = state.get("access_token", "")
    if not token:
        log(f"[{_bot_label(bot_id)}] no access token, cannot send")
        return False
    headers = {
        "Authorization": f"QQBot {token}",
        "Content-Type": "application/json"
    }
    body = {"content": text, "msg_type": 0}
    if msg_id:
        body["msg_id"] = msg_id

    url = f"{QQ_API_BASE}/v2/users/{openid}/messages"
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(url, headers=headers,
                content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
            if r.status_code != 200:
                log(f"[{_bot_label(bot_id)}] send failed [{r.status_code}]: {r.text[:200]}")
                return False
            else:
                log(f"[{_bot_label(bot_id)}] reply sent -> {openid}")
                return True
    except Exception as e:
        log(f"[{_bot_label(bot_id)}] send error: {type(e).__name__}: {e}")
        return False


# ══════════════════════════════════════════════
#  AI API
# ══════════════════════════════════════════════

def _clean_tool_messages(msgs: list[dict]) -> list[dict]:
    """Remove orphaned tool_result blocks whose tool_use was trimmed."""
    known_ids = set()
    for m in msgs:
        content = m.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    known_ids.add(block.get("id", ""))
    cleaned = []
    for m in msgs:
        content = m.get("content", [])
        if isinstance(content, list) and len(content) == 1:
            block = content[0]
            if isinstance(block, dict) and block.get("type") == "tool_result":
                if block.get("tool_use_id") not in known_ids:
                    continue  # skip orphaned tool_result
        cleaned.append(m)
    return cleaned


async def route_message(bot_id: str, user_text: str, history: list[dict]) -> dict:
    """Route via AI: chat for simple chat, forward_task for Claude Code.
    Returns {reply: str, task: str|None, query_question: str|None}."""
    messages = _clean_tool_messages(list(history[-12:]))
    messages.append({"role": "user", "content": user_text})

    bot_cfg = _bot_configs.get(bot_id, {})
    bot_name = bot_cfg.get("name", bot_id)
    custom_prompt = bot_cfg.get("system_prompt", "")
    if custom_prompt:
        system_prompt = custom_prompt.replace("{name}", bot_name)
        system_prompt += "\n\n" + ROUTE_RULES
    else:
        system_prompt = ROUTE_SYSTEM_PROMPT.replace("{name}", bot_name)
    system_prompt += "\n\n" + CURRENT_PROJECT
    if os.path.exists(CONTEXT_PATH):
        ctx = open(CONTEXT_PATH, encoding="utf-8").read().strip()
        if ctx:
            system_prompt += "\n\n[当前项目状态]\n" + ctx

    api_key = bot_cfg.get("api_key", "")
    api_base = bot_cfg.get("api_base_url", "")
    model = bot_cfg.get("model", "")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": model,
        "max_tokens": 2000,
        "system": system_prompt,
        "messages": messages,
        "tools": [FORWARD_TOOL, QUERY_TOOL],
        "thinking": {"type": "enabled", "budget_tokens": 200},
    }

    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(f"{api_base}/v1/messages", headers=headers, json=body)
        if r.status_code != 200:
            log(f"API error [{r.status_code}]: {r.text[:200]}")
            return {"reply": "(唔，我有点走神…等下哈)", "task": user_text}

        data = json.loads(r.text.encode("utf-8").decode("utf-8-sig"))
        tool_calls = []
        text_parts = []

        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                tool_calls.append(block)
            elif block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        if tool_calls:
            tc = tool_calls[0]
            tool_name = tc.get("name", "")
            inp = tc.get("input", {})

            if tool_name == "query_claude":
                reply = inp.get("reply_to_user", "等我问问老大哥哈")
                question = inp.get("question", user_text)
                log(f"kunkun query: {question[:60]}")

                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": data["content"]})
                history.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": "已记录问题，等老大哥回复。"
                }]})
                return {"reply": reply, "task": None, "query_question": question}

            # forward_task → forward to Claude
            # Always use original user_text — don't let AI rewrite
            task = user_text
            reply = inp.get("reply_to_user", "收到，交给我了")
            log(f"forward to Claude: {task[:60]}")

            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": data["content"]})
            history.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": "已转发给 Claude Code 处理。"
            }]})
            return {"reply": reply, "task": task}

        # No tool called — chat reply, don't forward
        reply = "".join(text_parts).strip() or "嗯嗯~"
        log(f"chat reply: {reply[:60]}")
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        return {"reply": reply, "task": None}


# ══════════════════════════════════════════════
#  Task queue (AI-routed tasks)
# ══════════════════════════════════════════════

def queue_task(bot_id: str, user_id: str, content: str, msg_id: str):
    items = read_json(TASK_INBOX)
    for item in items:
        if item.get("msg_id") == msg_id and item.get("status") in ("pending", "forwarded"):
            return
    items.append({
        "id": str(uuid.uuid4())[:8],
        "bot_id": bot_id,
        "user_id": user_id,
        "msg_id": msg_id,
        "content": content,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "pending"
    })
    if len(items) > 50:
        items = items[-50:]
    write_json(TASK_INBOX, items)
    log(f"[{_bot_label(bot_id)}] task queued: {content[:60]}")


# ══════════════════════════════════════════════
#  HTTP server + bot management API
# ══════════════════════════════════════════════

def _verify_auth(request: str) -> bool:
    if not BRIDGE_TOKEN:
        return True
    m = _re.search(r"Authorization:\s*Bearer\s+(\S+)", request, _re.IGNORECASE)
    return m is not None and m.group(1) == BRIDGE_TOKEN


def _parse_body(raw_body: bytes) -> dict:
    request = raw_body.decode("utf-8", errors="replace")
    cl_match = _re.search(r"Content-Length:\s*(\d+)", request, _re.IGNORECASE)
    body_len = int(cl_match.group(1)) if cl_match else 0
    header_end = raw_body.find(b"\r\n\r\n") + 4
    body = raw_body[header_end:][:body_len]
    body_str = body.decode("utf-8", errors="replace").strip()
    return json.loads(body_str) if body_str else {}


async def _start_bot_ws(bot_id: str) -> bool:
    """Start a ws_loop task for a specific bot. Returns True if started."""
    state = _bot_state.get(bot_id)
    if not state:
        return False
    if state.get("ws_task") and not state["ws_task"].done():
        return False  # already running
    state["running"] = True
    state["ws_task"] = asyncio.create_task(_bot_ws_runner(bot_id))
    log(f"[{_bot_label(bot_id)}] ws task started")
    return True


async def _stop_bot_ws(bot_id: str):
    """Stop a bot's ws_loop task."""
    state = _bot_state.get(bot_id)
    if not state:
        return
    state["running"] = False
    task = state.get("ws_task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    state["ws_task"] = None
    log(f"[{_bot_label(bot_id)}] ws task stopped")


async def http_handler(reader, writer):
    try:
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
            if not chunk:
                break
            raw += chunk
        request = raw.decode("utf-8", errors="replace")

        # Ensure full body is read (previous loop only reads to end of headers)
        cl_match = _re.search(r"Content-Length:\s*(\d+)", request, _re.IGNORECASE)
        body_len = int(cl_match.group(1)) if cl_match else 0
        header_end = raw.index(b"\r\n\r\n") + 4
        while len(raw) - header_end < body_len:
            chunk = await asyncio.wait_for(reader.read(body_len), timeout=3)
            if not chunk:
                break
            raw += chunk

        if not _verify_auth(request):
            writer.write(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
            await writer.drain()
            return

        data = _parse_body(raw)

        if "POST /reply" in request:
            user_id = data.get("user_id", "0D5531D06F4FC10669A8A70B92423827")
            msg_id = data.get("msg_id", "")
            text = data.get("text", "")
            bot_id = data.get("bot_id", _default_bot_id or "")
            ok = await send_qq_message(bot_id, user_id, text, msg_id)
            if ok:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            else:
                body = b"QQ API failed"
                writer.write(f"HTTP/1.1 502 Bad Gateway\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)

        elif "POST /bot/start" in request:
            bot_id = data.get("bot_id", "")
            # Dynamically register bot if not in config
            if bot_id not in _bot_configs and "qq_app_id" in data:
                _bot_configs[bot_id] = {
                    "id": bot_id,
                    "name": data.get("bot_name", bot_id),
                    "session_index": data.get("session_index", 0),
                    "qq_app_id": data.get("qq_app_id", ""),
                    "qq_secret": data.get("qq_secret", ""),
                    "provider": data.get("provider", ""),
                    "api_key": data.get("api_key", ""),
                    "api_base_url": data.get("api_base_url", ""),
                    "model": data.get("model", ""),
                    "system_prompt": data.get("system_prompt", ""),
                }
                _bot_state[bot_id] = {"ws_task": None, "ws_url": "", "running": False, "retry": 0}
                log(f"[{_bot_label(bot_id)}] dynamically registered")
            ok = await _start_bot_ws(bot_id) if bot_id in _bot_configs else False
            body = f'{{"ok": {str(ok).lower()}, "bot_id": "{bot_id}"}}'
            body_bytes = body.encode()
            writer.write(f"HTTP/1.1 {'200' if ok else '400'} OK\r\nContent-Length: {len(body_bytes)}\r\n\r\n".encode() + body_bytes)

        elif "POST /bot/stop" in request:
            bot_id = data.get("bot_id", "")
            if bot_id in _bot_configs:
                await _stop_bot_ws(bot_id)
            body = f'{{"ok": true, "bot_id": "{bot_id}"}}'
            body_bytes = body.encode()
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body_bytes)}\r\n\r\n".encode() + body_bytes)

        elif "GET /bots/status" in request:
            status = {}
            for bid, st in _bot_state.items():
                status[bid] = {"running": st.get("running", False), "name": _bot_configs[bid]["name"]}
            body = json.dumps(status, ensure_ascii=False)
            body_bytes = body.encode()
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body_bytes)}\r\n\r\n".encode() + body_bytes)

        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
    except Exception as e:
        log(f"http error: {e}")
        try:
            writer.write(b"HTTP/1.1 500 Error\r\n\r\n")
        except Exception:
            pass
    finally:
        try:
            await writer.drain()
            writer.close()
        except Exception:
            pass


# ══════════════════════════════════════════════
#  WebSocket event loop (per bot)
# ══════════════════════════════════════════════

async def _bot_ws_runner(bot_id: str):
    """Reconnect loop for a single bot's WebSocket."""
    state = _bot_state.get(bot_id)
    if not state:
        return
    backoff = 1
    while state.get("running"):
        if not check_watchdog():
            log(f"[{_bot_label(bot_id)}] watchdog lost, stopping")
            state["running"] = False
            break
        try:
            if time.time() > state.get("token_expiry", 0):
                await refresh_token(bot_id)
            await _ws_loop(bot_id)
            backoff = 1
        except websockets.ConnectionClosed as e:
            log(f"[{_bot_label(bot_id)}] connection closed: {e}")
        except Exception as e:
            log(f"[{_bot_label(bot_id)}] error: {type(e).__name__}: {e}")
        backoff = min(backoff * 2, 60)
        log(f"[{_bot_label(bot_id)}] reconnect in {backoff}s...")
        await asyncio.sleep(backoff)
    log(f"[{_bot_label(bot_id)}] ws runner ended")


async def _ws_loop(bot_id: str):
    state = _bot_state.get(bot_id, {})
    ws_url = await get_ws_url(bot_id)

    async with websockets.connect(ws_url, max_size=2**23) as ws:
        log(f"[{_bot_label(bot_id)}] websocket connected")

        hello = json.loads(await ws.recv())
        if hello.get("op") != 10:
            log(f"[{_bot_label(bot_id)}] expected Hello, got op={hello.get('op')}")
            return
        heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000
        log(f"[{_bot_label(bot_id)}] hello received, heartbeat={heartbeat_interval}s")

        token = state.get("access_token", "")
        identify = {
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": INTENTS,
                "shard": [0, 1],
                "properties": {}
            }
        }
        await ws.send(json.dumps(identify))
        log(f"[{_bot_label(bot_id)}] identify sent")

        async def heartbeat_loop():
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await ws.send(json.dumps({"op": 1, "d": state.get("last_seq")}))
                except Exception:
                    break

        hb_task = asyncio.create_task(heartbeat_loop())
        seen = _seen_msg_ids.setdefault(bot_id, set())
        histories = _chat_histories.setdefault(bot_id, {})

        try:
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                op = msg.get("op")
                seq = msg.get("s")
                if seq:
                    state["last_seq"] = seq

                if op == 0:
                    event_type = msg.get("t", "")
                    data = msg.get("d", {})

                    if event_type == "READY":
                        state["session_id"] = data.get("session_id")
                        log(f"[{_bot_label(bot_id)}] ready! session={state['session_id']}")

                    elif event_type == "C2C_MESSAGE_CREATE":
                        content = data.get("content", "")
                        msg_id = data.get("id", "")
                        user_id = data.get("author", {}).get("id", "")
                        log(f"[{_bot_label(bot_id)}] msg: {content[:80]}")

                        if not content.strip():
                            continue

                        if msg_id in seen:
                            continue
                        seen.add(msg_id)
                        if len(seen) > 200:
                            keep = list(seen)[-100:]
                            seen.clear()
                            seen.update(keep)

                        stripped = content.strip()

                        # Permission response check
                        if stripped.lower() in ("y", "yes", "n", "no", "确认", "允许", "取消", "拒绝"):
                            req_file = os.path.join(DATA_DIR, "permission-request.json")
                            if os.path.exists(req_file):
                                try:
                                    with open(req_file, encoding="utf-8") as f:
                                        perm_requests = json.load(f)
                                    pending = [r for r in perm_requests if r.get("status") == "pending"]
                                    if pending:
                                        approved = stripped.lower() in ("y", "yes", "确认", "允许")
                                        resp = {"id": pending[0]["id"], "approved": approved,
                                                "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
                                        resp_file = os.path.join(DATA_DIR, "permission-response.json")
                                        responses = []
                                        if os.path.exists(resp_file):
                                            with open(resp_file, encoding="utf-8") as f:
                                                responses = json.load(f)
                                        responses.append(resp)
                                        with open(resp_file, "w", encoding="utf-8") as f:
                                            json.dump(responses, f, ensure_ascii=False, indent=2)
                                        for r in perm_requests:
                                            if r["id"] == pending[0]["id"]:
                                                r["status"] = "resolved"
                                        with open(req_file, "w", encoding="utf-8") as f:
                                            json.dump(perm_requests, f, ensure_ascii=False, indent=2)
                                        await send_qq_message(bot_id, user_id, "已批准执行" if approved else "已拒绝")
                                        continue
                                except Exception as e:
                                    log(f"[{_bot_label(bot_id)}] permission check error: {e}")

                        hist = histories.setdefault(user_id, [])

                        # Local prefix routing
                        local_route = None
                        for prefix, agent in ROUTE_PREFIXES.items():
                            if stripped.startswith(prefix + " ") or stripped == prefix:
                                local_route = agent
                                break

                        if local_route:
                            task = f"[{local_route}] {stripped}"
                            log(f"[{_bot_label(bot_id)}] local route ({local_route}): {stripped[:60]}")
                            queue_task(bot_id, user_id, task, msg_id)
                            await send_qq_message(bot_id, user_id, f"已转给 {local_route}-agent")
                        else:
                            local_reply = _local_quick_reply(stripped, bot_id)
                            if local_reply:
                                global _api_calls_saved
                                _api_calls_saved += 1
                                log(f"[{_bot_label(bot_id)}] local reply (saved #{_api_calls_saved}): {stripped[:40]}")
                                await send_qq_message(bot_id, user_id, local_reply)
                            else:
                                # Direct relay to Claude Code — bot IS Claude
                                queue_task(bot_id, user_id, stripped, msg_id)
                                log(f"[{_bot_label(bot_id)}] relay to Claude: {stripped[:60]}")
                        if len(hist) > 20:
                            histories[user_id] = hist[-20:]

                    elif event_type == "C2C_FRIEND_ADD":
                        log(f"[{_bot_label(bot_id)}] new friend: {data.get('openid', '?')}")

                elif op == 11:
                    pass
                elif op == 7:
                    log(f"[{_bot_label(bot_id)}] server requested reconnect")
                    break
                elif op == 9:
                    log(f"[{_bot_label(bot_id)}] invalid session, re-auth needed")
                    state["session_id"] = None
                    break
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass

        log(f"[{_bot_label(bot_id)}] ws loop ended")


# ══════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════

async def main():
    ensure_files()
    log(f"QQ Bridge starting (multi-bot + HTTP :{HTTP_PORT})...")

    http_server = await asyncio.start_server(http_handler, "127.0.0.1", HTTP_PORT)
    log(f"http server on :{HTTP_PORT}")

    # Don't auto-start bots — Hub controls lifecycle via /bot/start /bot/stop

    # Keep alive — check watchdog periodically
    while True:
        await asyncio.sleep(10)
        if not check_watchdog():
            log("watchdog heartbeat lost — shutting down")
            for bot_id in list(_bot_configs.keys()):
                await _stop_bot_ws(bot_id)
            break

    log("QQ Bridge shut down")


if __name__ == "__main__":
    asyncio.run(main())
