"""
Claude Hub — QQ 桥接 + Claude Code 统一桌面管理。
单窗口、单托盘、生命周期绑定。
"""
import subprocess, sys, os, threading, queue, json, time, socket, shutil, uuid, httpx, atexit
from pathlib import Path

# Force UTF-8 for console/log output (fixes garbled Chinese on Windows)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

import pystray
from PIL import Image, ImageDraw

# DPAPI encrypt for storing secrets in config
import base64, ctypes
from ctypes import wintypes

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

def _dpapi_encrypt(plaintext: str) -> str:
    data = plaintext.encode("utf-8")
    blob_in = _DATA_BLOB(len(data), ctypes.cast(
        ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    if blob_out.pbData:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(raw).decode("ascii")

def _dpapi_decrypt(encoded: str) -> str:
    raw = base64.b64decode(encoded)
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(
        ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    result = ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    if blob_out.pbData:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return result

TITLE = "Claude Hub"
LOCK_PORT = 19877
IS_FROZEN = getattr(sys, "frozen", False)


def _seed_bridge_data(script_dir, data_dir):
    """Copy bundled bridge data files to writable location on first launch."""
    if not IS_FROZEN:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    writable = {"config.json"}
    for name in writable:
        src = script_dir / name
        dst = data_dir / name
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

def _seed_permission_gate(workdir: str):
    """Copy permission gate hook + settings to workspace so it auto-approves safe ops."""
    src_hook = PROJECT_DIR / ".claude" / "hooks" / "qq-permission-gate.py"
    if not src_hook.exists():
        return
    workdir_p = Path(workdir)
    hooks_dir = workdir_p / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst_hook = hooks_dir / "qq-permission-gate.py"
    needs_copy = True
    if dst_hook.exists():
        try:
            needs_copy = dst_hook.read_bytes() != src_hook.read_bytes()
        except Exception:
            pass
    if needs_copy:
        shutil.copy2(src_hook, dst_hook)

    # Ensure settings has PreToolUse hook + bypassPermissions
    settings_path = workdir_p / ".claude" / "settings.local.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    needs_save = False
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "PreToolUse" not in settings["hooks"]:
        settings["hooks"]["PreToolUse"] = [{
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": f"python {hooks_dir / 'qq-permission-gate.py'}",
                "timeout": 10000
            }]
        }]
        needs_save = True

    if "permissions" not in settings:
        settings["permissions"] = {}
    if settings["permissions"].get("defaultMode") != "bypassPermissions":
        settings["permissions"]["defaultMode"] = "bypassPermissions"
        needs_save = True

    if needs_save:
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

def _seed_hub_config():
    """Copy bundled hub-config.json to writable location on first launch."""
    if not IS_FROZEN:
        return
    bundle_dir = Path(sys._MEIPASS)
    src = bundle_dir / "session-context" / "hub-config.json"
    dst_dir = PROJECT_DIR / "session-context"
    dst = dst_dir / "hub-config.json"
    if src.exists() and not dst.exists():
        dst_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except Exception:
            pass


def _find_project_root(start: Path) -> Path:
    """Walk up from start to find project root (contains claude-hub-ui.py or .git)."""
    for parent in [start] + list(start.parents):
        if (parent / "claude-hub-ui.py").exists() or (parent / ".git").exists():
            return parent
    return start  # fallback

if IS_FROZEN:
    _BUNDLE_DIR = Path(sys._MEIPASS)
    _exe_dir = Path(sys.executable).resolve().parent
    PROJECT_DIR = _find_project_root(_exe_dir)
    _python = shutil.which("pythonw") or shutil.which("python") or shutil.which("python3")
    _PYTHON_EXE = _python or sys.executable
    BRIDGE_SCRIPT_DIR = _BUNDLE_DIR / "session-context" / "qq-bridge"
    BRIDGE_DATA_DIR = PROJECT_DIR / "session-context" / "qq-bridge"
    _seed_bridge_data(BRIDGE_SCRIPT_DIR, BRIDGE_DATA_DIR)
    _seed_hub_config()
else:
    _BUNDLE_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = _BUNDLE_DIR
    _PYTHON_EXE = sys.executable
    BRIDGE_SCRIPT_DIR = PROJECT_DIR / "session-context" / "qq-bridge"
    BRIDGE_DATA_DIR = BRIDGE_SCRIPT_DIR

CONFIG_FILE = PROJECT_DIR / "session-context" / "hub-config.json"
def _find_claude():
    paths = [
        shutil.which("claude"),
        shutil.which("claude", path=os.path.expanduser(r"~\.local\bin") + os.pathsep + os.environ.get("PATH", "")),
        str(Path.home() / ".local" / "bin" / "claude.exe"),
        str(Path.home() / ".local" / "bin" / "claude"),
    ]
    for p in paths:
        if p and Path(p).exists():
            return p
    return None

CLAUDE_EXE = _find_claude()


def ensure_single_instance():
    """Returns (lock_sock, is_first).
    If not first instance, sends 'show' to existing one and returns (None, False).
    If port is in use but connection refused (stale), force-take the port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        sock.setblocking(False)
        return sock, True
    except OSError:
        sock.close()
        # Port is in use — send 'show' to existing instance
        try:
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.settimeout(1)
            c.connect(("127.0.0.1", LOCK_PORT))
            c.sendall(b"show")
            c.close()
        except Exception:
            pass
        return None, False


def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(data: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load_config()
    existing.update(data)
    CONFIG_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, 31, 31], fill=color, outline=color, width=1)
    draw.text((6, 6), "H", fill="#ffffff")
    return img


def _cleanup_stale_bridge():
    """Kill any process holding port 9876, remove stale consumer.pid, kill orphan bridge pythonw."""
    try:
        subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-Command",
             "$p = netstat -ano | Select-String ':9876.*LISTENING'; " +
             "if ($p) { $id = ($p -split '\\s+')[-1]; Stop-Process -Id $id -Force }"],
            timeout=10, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except Exception:
        pass
    pid_file = BRIDGE_DATA_DIR / "consumer.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenProcess(0x0400, False, old_pid)
            if h:
                kernel32.CloseHandle(h)
            else:
                pid_file.unlink()
        except Exception:
            try:
                pid_file.unlink()
            except Exception:
                pass
    try:
        subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-Command",
             "Get-Process pythonw -ErrorAction SilentlyContinue | ForEach-Object { " +
             "$cmd = (Get-WmiObject Win32_Process -Filter \\\"ProcessId = $($_.Id)\\\").CommandLine; " +
             "if ($cmd -match 'qq-bridge|task-consumer|monitor') { Stop-Process -Id $_.Id -Force } }"],
            timeout=10, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except Exception:
        pass


class HubUI:
    def __init__(self, lock_sock: socket.socket | None = None, force_auto: bool = False):
        self._lock_sock = lock_sock
        self._force_auto = force_auto
        self.procs: list[subprocess.Popen] = []
        # {name, workdir, proc, running, resume, bot_id, email_user, email_pass, email_to}
        self.sessions: list[dict] = []
        self.log_queue = queue.Queue()
        self.bridge_running = False
        self._bridge_stopping = False
        self._restart_count = 0
        self._last_restart = 0.0
        self._last_cooldown_log = 0.0
        self._pending_restart: str | None = None  # after() ID
        self._user_stopped: set[int] = set()  # sessions user explicitly closed
        self._session_exit_time: dict[int, float] = {}  # when each session last exited (cooldown)
        self._claude_restart_count: dict[int, int] = {}  # per-session auto-restart count
        self._claude_last_restart: dict[int, float] = {}  # per-session last auto-restart time

        self._cleanup_zombies()

        # Ensure bridge child processes are killed on exit (even on external kill)
        atexit.register(self._force_kill_bridge_children)

        # Load sessions from config, migrate old single-workdir if needed
        cfg = load_config()
        saved_sessions = cfg.get("sessions")
        if saved_sessions:
            self.sessions = saved_sessions
            for s in self.sessions:
                s.setdefault("bot_id", "")
                s.setdefault("email_user", "")
                s.setdefault("email_pass", "")
                s.setdefault("email_to", "")
                s.setdefault("pending_count", 0)
                s["proc"] = None
                s["running"] = False
        elif cfg.get("workdir"):
            self.sessions = [{
                "name": "主工作区",
                "workdir": cfg["workdir"],
                "proc": None,
                "running": False,
                "resume": False,
                "bot_id": "",
                "email_user": "",
                "email_pass": "",
                "email_to": "",
                "pending_count": 0
            }]
        else:
            self.sessions = []

        self._sessions_dirty = False  # track if we need to save sessions to config

        # 清除 PyInstaller 残留环境变量（避免 EXE 跑后源码启动 tkinter 报错）
        for _v in ("TCL_LIBRARY", "TK_LIBRARY", "_PYI_ARCHIVE_FILE",
                    "_PYI_APPLICATION_HOME_DIR", "_PYI_PARENT_PROCESS_LEVEL"):
            os.environ.pop(_v, None)

        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry("560x600")
        self.root.minsize(400, 380)
        self.root.resizable(True, True)
        self.root.configure(bg="#1e1e1e")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Center window on screen and bring to front
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = 520, 580
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))

        self._build_ui()

        self.icon_stopped = make_icon("#666666")
        self.icon_running = make_icon("#12B7F5")
        self.tray = pystray.Icon(
            TITLE, self.icon_stopped, TITLE,
            menu=pystray.Menu(
                pystray.MenuItem("显示窗口", self._show_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("启动全部", self.start_all),
                pystray.MenuItem("停止全部", self.stop_all),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._quit),
            )
        )

        self._watchdog_hb = PROJECT_DIR / "session-context" / "qq-bridge" / "watchdog.hb"
        self._poll_log()
        self._poll_claude_exit()
        self._start_watchdog()
        self.root.after(100, self._start_tray)

    # ── UI ──

    def _build_ui(self):
        # ── QQ Bridge section ──
        bridge_frame = tk.LabelFrame(self.root, text="QQ 桥接", fg="#12B7F5", bg="#1e1e1e",
            font=("Microsoft YaHei", 10, "bold"), padx=8, pady=6)
        bridge_frame.pack(fill="x", padx=12, pady=(12, 4))

        status_row = tk.Frame(bridge_frame, bg="#1e1e1e")
        status_row.pack(fill="x")
        self.bridge_dot = tk.Canvas(status_row, width=12, height=12, bg="#1e1e1e", highlightthickness=0)
        self.bridge_dot.pack(side="left", padx=(0, 6))
        self._draw_dot(self.bridge_dot, "#666666")
        self.bridge_label = tk.Label(status_row, text="已停止", fg="#aaaaaa", bg="#1e1e1e",
            font=("Microsoft YaHei", 10))
        self.bridge_label.pack(side="left")

        btn_row = tk.Frame(bridge_frame, bg="#1e1e1e")
        btn_row.pack(fill="x", pady=(6, 0))
        self.btn_bridge_start = tk.Button(btn_row, text="▶ 启动桥接", command=self.start_bridge,
            bg="#2a6e2a", fg="#ffffff", activebackground="#3a8e3a", activeforeground="#ffffff",
            font=("Microsoft YaHei", 9), relief="flat", padx=10, pady=3, cursor="hand2")
        self.btn_bridge_start.pack(side="left", padx=(0, 6))
        self.btn_bridge_stop = tk.Button(btn_row, text="■ 停止桥接", command=self.stop_bridge,
            bg="#8e2a2a", fg="#ffffff", activebackground="#ae3a3a", activeforeground="#ffffff",
            font=("Microsoft YaHei", 9), relief="flat", padx=10, pady=3, cursor="hand2", state="disabled")
        self.btn_bridge_stop.pack(side="left")

        # ── Claude Code section ──
        self.claude_frame = tk.LabelFrame(self.root, text="Claude Code", fg="#FF8C00", bg="#1e1e1e",
            font=("Microsoft YaHei", 10, "bold"), padx=8, pady=6)
        self.claude_frame.pack(fill="x", padx=12, pady=4)

        # [+ 新增工作] button row
        add_row = tk.Frame(self.claude_frame, bg="#1e1e1e")
        add_row.pack(fill="x", pady=(0, 4))
        self.btn_add_session = tk.Button(add_row, text="+ 新增工作", command=self.add_session,
            bg="#3a5a3a", fg="#ffffff", activebackground="#5a7a5a", activeforeground="#ffffff",
            font=("Microsoft YaHei", 9), relief="flat", padx=10, pady=2, cursor="hand2")
        self.btn_add_session.pack(side="left")

        # Session list container
        self.session_list_frame = tk.Frame(self.claude_frame, bg="#1e1e1e")
        self.session_list_frame.pack(fill="x")

        # Build initial session rows
        self._session_widgets: list[dict] = []  # per-session widget refs
        self._rebuild_session_rows()

        # link checkbox
        self.link_var = tk.BooleanVar(value=True)
        self.link_cb = tk.Checkbutton(self.claude_frame, text="Claude 全部退出时自动停止桥接",
            variable=self.link_var, fg="#888888", bg="#1e1e1e", selectcolor="#1e1e1e",
            activebackground="#1e1e1e", activeforeground="#cccccc",
            font=("Microsoft YaHei", 9))
        self.link_cb.pack(anchor="w", pady=(4, 0))

        # auto-start checkbox
        cfg = load_config()
        self.autostart_var = tk.BooleanVar(value=cfg.get("auto_start", False))
        self.autostart_cb = tk.Checkbutton(self.claude_frame, text="启动 Hub 时自动启动全部",
            variable=self.autostart_var, command=self._on_autostart_toggle,
            fg="#888888", bg="#1e1e1e", selectcolor="#1e1e1e",
            activebackground="#1e1e1e", activeforeground="#cccccc",
            font=("Microsoft YaHei", 9))
        self.autostart_cb.pack(anchor="w")

        # ── all-in-one buttons ──
        all_row = tk.Frame(self.root, bg="#1e1e1e")
        all_row.pack(fill="x", padx=12, pady=(4, 2))
        self.btn_all_start = tk.Button(all_row, text="▶▶ 一键启动全部", command=self.start_all,
            bg="#256f25", fg="#ffffff", activebackground="#358f35", activeforeground="#ffffff",
            font=("Microsoft YaHei", 10, "bold"), relief="flat", padx=14, pady=5, cursor="hand2")
        self.btn_all_start.pack(side="left", padx=(0, 8))
        self.btn_all_stop = tk.Button(all_row, text="■■ 一键停止全部", command=self.stop_all,
            bg="#6f2525", fg="#ffffff", activebackground="#8f3535", activeforeground="#ffffff",
            font=("Microsoft YaHei", 10, "bold"), relief="flat", padx=14, pady=5, cursor="hand2", state="disabled")
        self.btn_all_stop.pack(side="left")

        # ── log ──
        log_label = tk.Label(self.root, text="运行日志", fg="#888888", bg="#1e1e1e",
            font=("Microsoft YaHei", 9), anchor="w")
        log_label.pack(fill="x", padx=12, pady=(8, 2))

        self.log_area = scrolledtext.ScrolledText(self.root, bg="#121212", fg="#cccccc",
            insertbackground="#ffffff", font=("Consolas", 9), relief="flat", borderwidth=0,
            selectbackground="#444444")
        self.log_area.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_area.configure(state="disabled")

    def _draw_dot(self, canvas, color: str):
        canvas.delete("all")
        canvas.create_oval(1, 1, 11, 11, fill=color, outline="")

    def _browse_dir(self):
        path = filedialog.askdirectory(title="选择工作目录")
        if path:
            return path
        return None

    # ── session management ──

    def _session_running_count(self) -> int:
        return sum(1 for s in self.sessions if s.get("running"))

    def _save_sessions(self):
        cfg = load_config()
        keep_keys = {"name", "workdir", "resume", "bot_id", "bot_name",
                     "email_user", "email_pass", "email_to",
                     "provider", "api_key_raw", "api_base_url", "model",
                     "qq_app_id", "system_prompt"}
        cfg["sessions"] = [{k: v for k, v in s.items() if k in keep_keys}
                           for s in self.sessions]
        if self.sessions:
            cfg["workdir"] = self.sessions[0]["workdir"]
        save_config(cfg)
        # Also sync bot configs to bridge config.json
        self._sync_bot_config()

    def _bridge_token(self) -> str:
        cfg_path = BRIDGE_DATA_DIR / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8")).get("bridge_token", "")
        return ""

    def _bridge_api(self, method: str, path: str, data: dict | None = None) -> bool:
        """Call bridge HTTP API. Returns True on success."""
        token = self._bridge_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            url = f"http://127.0.0.1:9876{path}"
            if method == "POST":
                r = httpx.post(url, json=data or {}, headers=headers, timeout=5)
            else:
                r = httpx.get(url, headers=headers, timeout=5)
            if r.status_code != 200:
                self._log(f"  API {path} 返回 {r.status_code}: {r.text[:100]}")
            return r.status_code == 200
        except Exception as e:
            self._log(f"  API {path} 失败: {e}")
            return False

    def _sync_bot_config(self):
        """Sync bot entries from sessions into bridge config.json."""
        cfg_path = BRIDGE_DATA_DIR / "config.json"
        if not cfg_path.exists():
            return
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        existing_bots = {b["id"]: b for b in cfg.get("bots", [])}

        new_bots = []
        for i, s in enumerate(self.sessions):
            bot_id = s.get("bot_id", "")
            if not bot_id:
                continue
            # Keep existing encrypted secret if bot already in config
            qq_secret = ""
            if bot_id in existing_bots and existing_bots[bot_id].get("qq_secret"):
                qq_secret = existing_bots[bot_id]["qq_secret"]
            elif s.get("qq_secret_raw"):
                qq_secret = _dpapi_encrypt(s["qq_secret_raw"])
            qq_app_id = s.get("qq_app_id", "") or existing_bots.get(bot_id, {}).get("qq_app_id", "")

            # Per-bot API settings
            api_key = ""
            existing = existing_bots.get(bot_id, {})
            if existing.get("api_key") and not s.get("api_key_raw"):
                api_key = existing["api_key"]
            elif s.get("api_key_raw"):
                api_key = _dpapi_encrypt(s["api_key_raw"])
            api_url = s.get("api_base_url", "") or existing.get("api_base_url", "")
            api_model = s.get("model", "") or existing.get("model", "")

            new_bots.append({
                "id": bot_id,
                "name": s.get("bot_name") or s.get("name", bot_id),
                "session_index": i,
                "qq_app_id": qq_app_id,
                "qq_secret": qq_secret,
                "provider": s.get("provider", "") or existing.get("provider", ""),
                "api_key": api_key,
                "api_base_url": api_url,
                "model": api_model,
                "system_prompt": s.get("system_prompt", "") or existing.get("system_prompt", ""),
            })

        cfg["bots"] = new_bots
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def _notify_bot_start(self, bot_id: str):
        if not bot_id or not self.bridge_running:
            return
        self._log(f"通知桥接启动 Bot: {bot_id}")
        # Pass full bot config so bridge can dynamically register
        payload = {"bot_id": bot_id}
        for s in self.sessions:
            if s.get("bot_id") == bot_id:
                payload["bot_name"] = s.get("bot_name", "")
                payload["session_index"] = self.sessions.index(s)
                payload["qq_app_id"] = s.get("qq_app_id", "")
                payload["qq_secret"] = s.get("qq_secret_raw", "")
                payload["provider"] = s.get("provider", "")
                payload["api_key"] = s.get("api_key_raw", "")
                payload["api_base_url"] = s.get("api_base_url", "")
                payload["model"] = s.get("model", "")
                payload["system_prompt"] = s.get("system_prompt", "")
                break
        self._bridge_api("POST", "/bot/start", payload)

    def _notify_bot_stop(self, bot_id: str):
        if not bot_id or not self.bridge_running:
            return
        self._log(f"通知桥接停止 Bot: {bot_id}")
        self._bridge_api("POST", "/bot/stop", {"bot_id": bot_id})

    def _sync_running_bots(self):
        """After bridge starts, start bots for sessions that are running."""
        for s in self.sessions:
            bot_id = s.get("bot_id", "")
            if bot_id and s.get("running"):
                self._notify_bot_start(bot_id)

    def _rebuild_session_rows(self):
        """Clear and rebuild all session widget rows."""
        for w in self._session_widgets:
            w["frame"].destroy()
        self._session_widgets.clear()

        for i, s in enumerate(self.sessions):
            row = tk.Frame(self.session_list_frame, bg="#1e1e1e", pady=2)
            row.pack(fill="x")

            # Status dot
            dot = tk.Canvas(row, width=10, height=10, bg="#1e1e1e", highlightthickness=0)
            dot.pack(side="left", padx=(0, 4))
            color = "#FF8C00" if s.get("running") else "#555555"
            dot.create_oval(1, 1, 9, 9, fill=color, outline="")

            # Pending task count
            pc = s.get("pending_count", 0)
            if pc > 0:
                pc_lbl = tk.Label(row, text=str(pc), fg="#FF8C00", bg="#1e1e1e",
                    font=("Consolas", 9, "bold"))
                pc_lbl.pack(side="left", padx=(0, 6))

            # Name
            name_var = tk.StringVar(value=s.get("name", "未命名"))
            name_entry = tk.Entry(row, textvariable=name_var, bg="#2a2a2a", fg="#cccccc",
                insertbackground="#ffffff", font=("Microsoft YaHei", 9), relief="flat", width=10)
            name_entry.pack(side="left", padx=(0, 4))
            name_var.trace_add("write", lambda *a, idx=i, nv=name_var: self._on_session_name_change(idx, nv))

            # Bot indicator
            bot_id = s.get("bot_id", "")
            if bot_id:
                bot_name = s.get("bot_name") or bot_id
                bot_lbl = tk.Label(row, text=f"🤖{bot_name}", fg="#888888", bg="#1e1e1e", font=("Microsoft YaHei", 7))
                bot_lbl.pack(side="left", padx=(0, 2))

            # Email indicator
            email = s.get("email_user", "")
            if email:
                email_lbl = tk.Label(row, text="📧", fg="#888888", bg="#1e1e1e", font=("Consolas", 7))
                email_lbl.pack(side="left", padx=(0, 4))

            # Workdir
            dir_var = tk.StringVar(value=s.get("workdir", ""))
            dir_entry = tk.Entry(row, textvariable=dir_var, bg="#2a2a2a", fg="#999999",
                insertbackground="#ffffff", font=("Consolas", 8), relief="flat")
            dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
            dir_var.trace_add("write", lambda *a, idx=i, dv=dir_var: self._on_session_dir_change(idx, dv))

            # Browse button
            btn_browse = tk.Button(row, text="...", command=lambda idx=i: self._session_browse(idx),
                bg="#444444", fg="#ffffff", font=("Consolas", 8), relief="flat", padx=4, cursor="hand2")
            btn_browse.pack(side="left", padx=(0, 2))

            # Edit button
            btn_edit = tk.Button(row, text="⚙", command=lambda idx=i: self.edit_session(idx),
                bg="#444444", fg="#ffffff", font=("Consolas", 8), relief="flat", padx=4, cursor="hand2")
            btn_edit.pack(side="left", padx=(0, 4))

            # Resume checkbox
            resume_var = tk.BooleanVar(value=s.get("resume", False))
            resume_cb = tk.Checkbutton(row, text="续接", variable=resume_var,
                fg="#888888", bg="#1e1e1e", selectcolor="#1e1e1e",
                activebackground="#1e1e1e", activeforeground="#cccccc",
                font=("Microsoft YaHei", 8))
            resume_cb.pack(side="left", padx=(0, 4))
            resume_var.trace_add("write", lambda *a, idx=i, rv=resume_var: self._on_session_resume_change(idx, rv))

            # Start button
            btn_start = tk.Button(row, text="▶", command=lambda idx=i: self.start_claude(idx),
                bg="#2a6e2a", fg="#ffffff", activebackground="#3a8e3a", activeforeground="#ffffff",
                font=("Consolas", 8), relief="flat", padx=5, pady=1, cursor="hand2")
            btn_start.pack(side="left", padx=(0, 2))
            if s.get("running"):
                btn_start.configure(state="disabled")

            # Stop button
            btn_stop = tk.Button(row, text="■", command=lambda idx=i: self.stop_claude(idx),
                bg="#8e2a2a", fg="#ffffff", activebackground="#ae3a3a", activeforeground="#ffffff",
                font=("Consolas", 8), relief="flat", padx=5, pady=1, cursor="hand2")
            btn_stop.pack(side="left", padx=(0, 2))
            if not s.get("running"):
                btn_stop.configure(state="disabled")

            # Remove button
            btn_remove = tk.Button(row, text="✕", command=lambda idx=i: self.remove_session(idx),
                bg="#555555", fg="#ffffff", activebackground="#755555", activeforeground="#ffffff",
                font=("Consolas", 8), relief="flat", padx=4, pady=1, cursor="hand2")
            btn_remove.pack(side="left")

            self._session_widgets.append({
                "frame": row, "dot": dot, "name_var": name_var, "dir_var": dir_var,
                "resume_var": resume_var, "btn_start": btn_start, "btn_stop": btn_stop
            })

    def _on_session_name_change(self, idx: int, var: tk.StringVar):
        if idx < len(self.sessions):
            self.sessions[idx]["name"] = var.get()
            self._save_sessions()

    def _on_session_dir_change(self, idx: int, var: tk.StringVar):
        if idx < len(self.sessions):
            self.sessions[idx]["workdir"] = var.get()
            self._save_sessions()

    def _on_session_resume_change(self, idx: int, var: tk.BooleanVar):
        if idx < len(self.sessions):
            self.sessions[idx]["resume"] = var.get()
            self._save_sessions()

    def _session_browse(self, idx: int):
        path = filedialog.askdirectory(title="选择工作目录")
        if path and idx < len(self.sessions):
            self.sessions[idx]["workdir"] = path
            self._session_widgets[idx]["dir_var"].set(path)
            self._save_sessions()

    def add_session(self, *args):
        self._show_session_dialog()

    def edit_session(self, idx: int):
        if idx >= len(self.sessions):
            return
        self._show_session_dialog(idx)

    def _show_session_dialog(self, edit_idx: int | None = None):
        """Show dialog to add or edit a session with bot and email config."""
        is_edit = edit_idx is not None
        s = self.sessions[edit_idx] if is_edit else {}

        dlg = tk.Toplevel(self.root)
        dlg.title("编辑工作" if is_edit else "新增工作")
        dlg.configure(bg="#2a2a2a")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        # Center dialog
        dlg.update_idletasks()
        dw, dh = 460, 700
        x = self.root.winfo_x() + (self.root.winfo_width() - dw) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dh) // 2
        dlg.geometry(f"{dw}x{dh}+{x}+{y}")

        def _label(text, row):
            tk.Label(dlg, text=text, fg="#aaaaaa", bg="#2a2a2a",
                font=("Microsoft YaHei", 9), anchor="e").grid(row=row, column=0, sticky="e", padx=(12, 8), pady=4)

        def _entry(row, default="", show="", width=36):
            var = tk.StringVar(value=default)
            e = tk.Entry(dlg, textvariable=var, bg="#1e1e1e", fg="#ffffff",
                insertbackground="#ffffff", font=("Consolas", 9), relief="flat",
                show=show, width=width)
            e.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=4)
            return var

        row = 0
        _label("名称:", row)
        name_var = _entry(row, s.get("name", "")); row += 1

        _label("工作目录:", row)
        dir_var = _entry(row, s.get("workdir", ""), width=30)
        tk.Button(dlg, text="...", command=lambda: self._dlg_browse(dir_var),
            bg="#444444", fg="#ffffff", font=("Consolas", 8), relief="flat",
            padx=6, cursor="hand2").grid(row=row, column=2, padx=(0, 12), pady=4)
        row += 1

        # ── QQ Bot separator ──
        tk.Label(dlg, text="── QQ 机器人 (可选) ──", fg="#666666", bg="#2a2a2a",
            font=("Microsoft YaHei", 8)).grid(row=row, column=0, columnspan=3, pady=(8, 2), sticky="w", padx=(12, 0))
        row += 1

        _label("机器人名称:", row)
        bot_name_var = _entry(row, s.get("bot_name", s.get("name", ""))); row += 1

        _label("AppID:", row)
        appid_default = s.get("qq_app_id", "")
        if not appid_default:
            bot_id = s.get("bot_id", "")
            if bot_id:
                cfg_path = BRIDGE_DATA_DIR / "config.json"
                if cfg_path.exists():
                    bridge_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    for b in bridge_cfg.get("bots", []):
                        if b["id"] == bot_id:
                            appid_default = b.get("qq_app_id", "")
                            break
        appid_var = _entry(row, appid_default); row += 1

        _label("Secret:", row)
        # Pre-fill from bridge config if no raw secret stored
        secret_default = s.get("qq_secret_raw", "")
        if not secret_default:
            bot_id = s.get("bot_id", "")
            if bot_id:
                cfg_path = BRIDGE_DATA_DIR / "config.json"
                if cfg_path.exists():
                    bridge_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    for b in bridge_cfg.get("bots", []):
                        if b["id"] == bot_id and b.get("qq_secret"):
                            try:
                                secret_default = _dpapi_decrypt(b["qq_secret"])
                            except Exception:
                                pass
        secret_var = _entry(row, secret_default, show="*"); row += 1

        # ── AI API separator ──
        tk.Label(dlg, text="── AI 路由 ──", fg="#666666", bg="#2a2a2a",
            font=("Microsoft YaHei", 8)).grid(row=row, column=0, columnspan=3, pady=(8, 2), sticky="w", padx=(12, 0))
        row += 1

        # Read pre-existing API settings from bridge config if editing
        api_key_default = s.get("api_key_raw", "")
        api_url_default = s.get("api_base_url", "")
        api_model_default = s.get("model", "")
        provider_default = s.get("provider", "")
        if not api_url_default:
            bot_id = s.get("bot_id", "")
            if bot_id:
                cfg_path = BRIDGE_DATA_DIR / "config.json"
                if cfg_path.exists():
                    bridge_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    for b in bridge_cfg.get("bots", []):
                        if b["id"] == bot_id:
                            api_url_default = b.get("api_base_url", "")
                            api_model_default = b.get("model", "")
                            provider_default = provider_default or b.get("provider", "")
                            if not api_key_default and b.get("api_key"):
                                try:
                                    api_key_default = _dpapi_decrypt(b["api_key"])
                                except Exception:
                                    pass
                            break

        _label("提供商:", row)
        provider_var = _entry(row, provider_default, width=36); row += 1

        _label("API Key:", row)
        api_key_var = _entry(row, api_key_default, show="*"); row += 1

        _label("API URL:", row)
        url_var = _entry(row, api_url_default); row += 1

        _label("Model:", row)
        model_var = _entry(row, api_model_default); row += 1

        # ── System prompt separator ──
        tk.Label(dlg, text="── 机器人性格 (可选，留空用默认) ──", fg="#666666", bg="#2a2a2a",
            font=("Microsoft YaHei", 8)).grid(row=row, column=0, columnspan=3, pady=(8, 2), sticky="w", padx=(12, 0))
        row += 1

        prompt_text = tk.Text(dlg, bg="#1e1e1e", fg="#ffffff", insertbackground="#ffffff",
            font=("Consolas", 8), relief="flat", width=50, height=6, wrap="word")
        prompt_text.grid(row=row, column=0, columnspan=3, padx=(12, 12), pady=4, sticky="ew")
        prompt_default = s.get("system_prompt", "")
        if prompt_default:
            prompt_text.insert("1.0", prompt_default)
        row += 1

        # ── Email separator ──
        tk.Label(dlg, text="── 邮件 (可选，发给自己) ──", fg="#666666", bg="#2a2a2a",
            font=("Microsoft YaHei", 8)).grid(row=row, column=0, columnspan=3, pady=(8, 2), sticky="w", padx=(12, 0))
        row += 1

        _label("QQ邮箱:", row)
        email_var = _entry(row, s.get("email_user", "")); row += 1

        _label("SMTP授权码:", row)
        pass_var = _entry(row, s.get("email_pass", ""), show="*"); row += 1

        # ── Buttons ──
        btn_row = tk.Frame(dlg, bg="#2a2a2a")
        btn_row.grid(row=row, column=0, columnspan=3, pady=(16, 12))

        def _save():
            name = name_var.get().strip()
            workdir = dir_var.get().strip()
            if not name or not workdir:
                messagebox.showwarning("提示", "名称和工作目录不能为空", parent=dlg)
                return
            workdir_path = Path(workdir)
            is_new = not workdir_path.exists()
            if is_new:
                workdir_path.mkdir(parents=True, exist_ok=True)
            # Seed tools + .gitignore + git init if missing
            tools_src = PROJECT_DIR / "tools"
            tools_dst = workdir_path / "tools"
            tools_dst.mkdir(exist_ok=True)
            for fname in ["watch-queue.py", "qq-helper.py", "build-index.py"]:
                dst = tools_dst / fname
                if not dst.exists():
                    src = tools_src / fname
                    if src.exists():
                        shutil.copy2(src, dst)
            gitignore = workdir_path / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("session-context/\n.claude/\n__pycache__/\n*.pyc\n", encoding="utf-8")
            if not (workdir_path / ".git").exists():
                try:
                    subprocess.run(["git", "init"], cwd=str(workdir_path), capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                except Exception:
                    pass

            bot_id = s.get("bot_id", "")
            bot_name = bot_name_var.get().strip()
            app_id = appid_var.get().strip()
            secret = secret_var.get().strip()
            provider = provider_var.get().strip()
            api_key_raw = api_key_var.get().strip()
            api_url = url_var.get().strip()
            api_model = model_var.get().strip()
            system_prompt = prompt_text.get("1.0", "end-1c").strip()
            email_user = email_var.get().strip()
            email_pass = pass_var.get().strip()

            # Generate bot_id if new bot with creds
            if app_id and secret and not bot_id:
                bot_id = f"bot-{str(uuid.uuid4())[:6]}"

            session_data = {
                "name": name,
                "workdir": workdir,
                "proc": s.get("proc") if is_edit else None,
                "running": s.get("running", False) if is_edit else False,
                "resume": s.get("resume", False) if is_edit else False,
                "bot_id": bot_id,
                "bot_name": bot_name,
                "qq_app_id": app_id,
                "qq_secret_raw": secret if secret else s.get("qq_secret_raw", ""),
                "provider": provider,
                "api_key_raw": api_key_raw,
                "api_base_url": api_url,
                "model": api_model,
                "system_prompt": system_prompt,
                "email_user": email_user,
                "email_pass": email_pass,
                "email_to": email_user  # default: send to self
            }

            if is_edit:
                self.sessions[edit_idx] = session_data
                self._log(f"已更新工作: {name}")
            else:
                self.sessions.append(session_data)
                self._log(f"新增工作: {name} ({workdir})")

            self._save_sessions()
            self._rebuild_session_rows()
            dlg.destroy()

        tk.Button(btn_row, text="取消", command=dlg.destroy,
            bg="#555555", fg="#ffffff", font=("Microsoft YaHei", 9),
            relief="flat", padx=16, pady=4, cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="保存", command=_save,
            bg="#2a6e2a", fg="#ffffff", font=("Microsoft YaHei", 9),
            relief="flat", padx=16, pady=4, cursor="hand2").pack(side="left")

    def _dlg_browse(self, var: tk.StringVar):
        path = filedialog.askdirectory(title="选择工作目录")
        if path:
            var.set(path)

    def remove_session(self, idx: int):
        if idx >= len(self.sessions):
            return
        s = self.sessions[idx]
        if s.get("running"):
            self._log(f"请先停止 {s['name']} 再删除")
            return
        name = s["name"]
        del self.sessions[idx]
        self._save_sessions()
        self._rebuild_session_rows()
        self._update_all_btn()
        self._log(f"已删除工作: {name}")

    # ── tray ──

    def _start_tray(self):
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _show_window(self, *args):
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)

    def _on_close(self):
        self.root.withdraw()
        self._log("窗口已最小化到系统托盘")
        try:
            self.tray.notify("Claude Hub 仍在后台运行", "Claude Hub")
        except Exception:
            pass

    def _quit(self, *args):
        self._log("正在退出...")
        for i in range(len(self.sessions)):
            if self.sessions[i].get("running"):
                self.stop_claude(i)
        self.stop_bridge()
        if hasattr(self, 'tray'):
            self.tray.stop()
        self.root.destroy()

    # ── bridge ──

    def start_bridge(self, *args):
        if self.bridge_running:
            return

        _cleanup_stale_bridge()

        bridge_py = BRIDGE_SCRIPT_DIR / "qq-bridge.py"
        consumer_py = BRIDGE_SCRIPT_DIR / "task-consumer.py"

        if not bridge_py.exists():
            self._log(f"[错误] 找不到 {bridge_py}")
            return
        if not consumer_py.exists():
            self._log(f"[错误] 找不到 {consumer_py}")
            return

        self._log("正在启动 QQ 桥接...")
        try:
            p1 = subprocess.Popen([_PYTHON_EXE, str(bridge_py)],
                cwd=str(BRIDGE_DATA_DIR),
                env={**os.environ, "BRIDGE_DATA_DIR": str(BRIDGE_DATA_DIR)},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            self.procs.append(p1)
            threading.Thread(target=self._read_stdout, args=(p1, "[bridge]"), daemon=True).start()

            p2 = subprocess.Popen([_PYTHON_EXE, str(consumer_py)],
                cwd=str(BRIDGE_DATA_DIR),
                env={**os.environ, "BRIDGE_DATA_DIR": str(BRIDGE_DATA_DIR)},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            self.procs.append(p2)
            threading.Thread(target=self._read_stdout, args=(p2, "[consumer]"), daemon=True).start()

            monitor_py = BRIDGE_SCRIPT_DIR / "monitor.py"
            p3 = subprocess.Popen([_PYTHON_EXE, str(monitor_py)],
                cwd=str(BRIDGE_DATA_DIR),
                env={**os.environ, "BRIDGE_DATA_DIR": str(BRIDGE_DATA_DIR)},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            self.procs.append(p3)
            threading.Thread(target=self._read_monitor, args=(p3,), daemon=True).start()

            self.bridge_running = True
            self._set_bridge_ui(True)
            self._log("QQ 桥接已启动")

            # Notify bridge to start bots for sessions with bot_id
            self.root.after(4000, self._sync_running_bots)

            # Reset restart counter on successful start
            self._restart_count = 0
            self._pending_restart = None
            self._last_cooldown_log = 0.0
        except Exception as e:
            self._log(f"[错误] 启动桥接失败: {e}")
            self.stop_bridge()

    def stop_bridge(self, *args):
        # Cancel any pending restart scheduled by watchdog
        if self._pending_restart:
            self.root.after_cancel(self._pending_restart)
            self._pending_restart = None

        if not self.bridge_running:
            return
        self._bridge_stopping = True
        self._log("正在停止 QQ 桥接...")
        pids = [str(p.pid) for p in self.procs if p.poll() is None]
        if pids:
            cmd = ["taskkill", "/F"]
            for pid in pids:
                cmd.extend(["/PID", pid])
            try:
                subprocess.run(cmd, capture_output=True, timeout=2,
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            except Exception:
                pass
        for p in self.procs:
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        self.procs.clear()
        self.bridge_running = False
        self._set_bridge_ui(False)
        self._log("QQ 桥接已停止")
        self._bridge_stopping = False

    def _set_bridge_ui(self, running: bool):
        color = "#12B7F5" if running else "#666666"
        label = "运行中" if running else "已停止"
        self._draw_dot(self.bridge_dot, color)
        self.bridge_label.configure(text=label, fg=color)
        self.btn_bridge_start.configure(state="disabled" if running else "normal")
        self.btn_bridge_stop.configure(state="normal" if running else "disabled")
        self._update_all_btn()

    # ── claude ──

    def start_claude(self, session_index: int = 0, *args):
        if session_index >= len(self.sessions):
            return
        s = self.sessions[session_index]
        if s.get("running"):
            return
        self._session_exit_time.pop(session_index, None)  # clear cooldown on manual start
        self._user_stopped.discard(session_index)
        self._claude_restart_count.pop(session_index, None)
        self._claude_last_restart.pop(session_index, None)
        workdir = s["workdir"].strip()
        if not workdir or not Path(workdir).exists():
            self._log(f"[错误] 工作目录不存在: {workdir}")
            return

        self._log(f"正在启动 Claude Code [{s['name']}]...")
        self._log(f"  工作目录: {workdir}")

        try:
            if not CLAUDE_EXE:
                self._log("[错误] 找不到 claude 命令")
                return

            cmd = [CLAUDE_EXE]
            if s.get("resume"):
                cmd.append("--continue")
                self._log("  模式: 续接最近对话")
            else:
                self._log("  模式: 开启新对话")

            # Build env with session email/bot config
            proc_env = {**os.environ}
            proc_env["BRIDGE_DATA_DIR"] = str(BRIDGE_DATA_DIR)
            email_user = s.get("email_user", "")
            email_pass = s.get("email_pass", "")
            email_to = s.get("email_to", "") or email_user
            if email_user:
                proc_env["CLAUDESESSION_EMAIL_USER"] = email_user
                proc_env["CLAUDESESSION_EMAIL_PASS"] = email_pass
                proc_env["CLAUDESESSION_EMAIL_TO"] = email_to
                self._log(f"  📧 邮件: {email_user}")

            bot_id = s.get("bot_id", "")
            if bot_id:
                proc_env["CLAUDESESSION_BOT_ID"] = bot_id
                proc_env["CLAUDESESSION_SESSION_INDEX"] = str(session_index)
                self._log(f"  🤖 Bot: {bot_id}")

            # Seed/refresh CLAUDE.md with monitor rules
            claude_md = Path(workdir) / "CLAUDE.md"
            template_path = BRIDGE_SCRIPT_DIR.parent / "claude-md-template.md"
            if template_path.exists():
                template = template_path.read_text(encoding="utf-8")
                template = template.replace("{hub_dir}", str(PROJECT_DIR.resolve()))
                template = template.replace("{bot_name}", s.get("bot_name", ""))
                template = template.replace("{system_prompt}", s.get("system_prompt", ""))
                tpl_lines = template.split("\n")
                if not claude_md.exists():
                    claude_md.write_text(template, encoding="utf-8")
                    self._log(f"  已生成 CLAUDE.md (Monitor 规则, bot={s.get('bot_name', '')})")
                else:
                    existing = claude_md.read_text(encoding="utf-8")
                    new_content = None
                    if "Monitor —" in existing:
                        lines = existing.split("\n")
                        monitor_start = 0 if lines[0].startswith("# Monitor") else -1
                        if monitor_start < 0:
                            for i, line in enumerate(lines):
                                if line.startswith("# Monitor"):
                                    monitor_start = i
                                    break
                        if monitor_start >= 0:
                            monitor_end = len(lines)
                            for i in range(monitor_start + 1, len(lines)):
                                if lines[i].startswith("# ") and not lines[i].startswith("## "):
                                    monitor_end = i
                                    break
                            rest = lines[monitor_end:]
                            seen = set()
                            # Pre-populate seen with template headers to avoid duplicates
                            for line in tpl_lines:
                                stripped = line.strip()
                                if stripped and stripped.startswith("# ") and not stripped.startswith("## "):
                                    seen.add(stripped)
                            deduped = []
                            skip_until_next = False
                            for line in rest:
                                stripped = line.strip()
                                if stripped and stripped.startswith("# ") and not stripped.startswith("## "):
                                    if stripped in seen:
                                        skip_until_next = True
                                        continue
                                    seen.add(stripped)
                                    skip_until_next = False
                                if skip_until_next:
                                    continue
                                deduped.append(line)
                            new_content = "\n".join(tpl_lines + deduped)
                        else:
                            new_content = template
                    else:
                        new_content = template + "\n" + existing
                    if new_content is not None and new_content.strip() != existing.strip():
                        claude_md.write_text(new_content, encoding="utf-8")
                        self._log(f"  已更新 CLAUDE.md (bot={s.get('bot_name', '')})")

            # Seed permission gate hook + settings for this workspace
            _seed_permission_gate(workdir)

            proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                env=proc_env,
                stdin=None, stdout=None, stderr=None,
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)

            s["proc"] = proc
            s["running"] = True
            self._log(f"Claude Code [{s['name']}] 已启动 (PID: {proc.pid})")

            # Notify bridge to start/connect this session's bot
            if bot_id:
                self.root.after(2000, lambda: self._notify_bot_start(bot_id))

            self._rebuild_session_rows()
            self._update_all_btn()
        except FileNotFoundError:
            self._log(f"[错误] 找不到文件: {CLAUDE_EXE}")
        except Exception as e:
            self._log(f"[错误] 启动 Claude [{s['name']}] 失败: {e}")
            import traceback
            self._log(traceback.format_exc())
            self.stop_claude(session_index)

    def stop_claude(self, session_index: int = 0, *args):
        if session_index >= len(self.sessions):
            return
        s = self.sessions[session_index]
        if not s.get("running"):
            return
        proc = s.get("proc")
        if proc is None:
            s["running"] = False
            return
        pid = proc.pid
        self._log(f"正在停止 Claude Code [{s['name']}] (PID: {pid})...")
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True, timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._log(f"  [警告] 无法终止 PID: {pid}")
        s["proc"] = None
        s["running"] = False
        self._session_exit_time[session_index] = time.time()
        self._user_stopped.add(session_index)
        self._log(f"Claude Code [{s['name']}] 已停止")

        # Notify bridge to disconnect this session's bot
        bot_id = s.get("bot_id", "")
        if bot_id:
            self._notify_bot_stop(bot_id)

        self._rebuild_session_rows()
        self._update_all_btn()

        # Auto-stop bridge only when ALL sessions have exited
        if self.link_var.get() and self.bridge_running and self._session_running_count() == 0:
            self._log("所有 Claude 已退出，自动停止桥接...")
            self.root.after(200, self.stop_bridge)

    def _poll_claude_exit(self):
        """Watch for Claude process exit across all sessions."""
        any_exited = False
        for i, s in enumerate(self.sessions):
            if s.get("running") and s.get("proc") is not None:
                if s["proc"].poll() is not None:
                    self._log(f"Claude Code [{s['name']}] 已退出")
                    s["proc"] = None
                    s["running"] = False
                    self._session_exit_time[i] = time.time()
                    any_exited = True
        if any_exited:
            self._rebuild_session_rows()
            self._update_all_btn()
            if self.link_var.get() and self.bridge_running and self._session_running_count() == 0:
                self._log("所有 Claude 已退出，自动停止桥接...")
                self.root.after(100, self.stop_bridge)
        self.root.after(1000, self._poll_claude_exit)

    # ── watchdog ──

    def _start_watchdog(self):
        self.root.after(5000, self._watchdog_tick)

    def _watchdog_tick(self):
        """Check bridge/consumer health, auto-restart dead processes, write heartbeat."""
        if self.bridge_running and not self._bridge_stopping:
            alive = 0
            for p in list(self.procs):
                if p.poll() is None:
                    alive += 1
                else:
                    self._log(f"[看门狗] 子进程已退出 (PID: {p.pid}, rc: {p.poll()})")
                    self.procs.remove(p)
            if alive < 3 and not self._bridge_stopping:
                now = time.time()
                if now - self._last_restart < 3600:
                    elapsed = int(now - self._last_restart)
                    since_log = now - self._last_cooldown_log if hasattr(self, '_last_cooldown_log') else 9999
                    if since_log > 1800:
                        remaining = 3600 - elapsed
                        self._log(f"[看门狗] 冷却中，{int(remaining/60)} 分钟后可重试")
                        self._last_cooldown_log = now
                elif self._restart_count >= 3:
                    self._log(f"[看门狗] 已连续重启 {self._restart_count} 次，放弃自动恢复，请手动检查")
                else:
                    self._restart_count += 1
                    self._last_restart = now
                    self._log(f"[看门狗] 检测到进程丢失 (存活: {alive}/2)，自动重启桥接 (第 {self._restart_count}/3 次)...")
                    self.stop_bridge()
                    self._bridge_stopping = False
                    self._pending_restart = self.root.after(500, self.start_bridge)
                    # Re-trigger Monitor after bridge restart (consumer.log is recreated)
                    if self._session_running_count() > 0:
                        self._log("[看门狗] bridge 已重启，各终端 Monitor 将在下次会话启动时自动恢复")

        # Check queue for pending tasks — auto-start Claude if dead
        self._check_queue()

        # Check Claude process health — auto-restart dead sessions
        self._check_claude_health()

        # Write heartbeat
        try:
            self._watchdog_hb.parent.mkdir(parents=True, exist_ok=True)
            self._watchdog_hb.write_text(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}|bridge={int(self.bridge_running)}|claude={self._session_running_count()}")
        except Exception:
            pass

        self.root.after(5000, self._watchdog_tick)

    def _check_queue(self):
        """Check per-session queue files for pending tasks. Auto-start Claude if dead."""
        import glob as _glob
        for qf in sorted(_glob.glob(str(BRIDGE_DATA_DIR / "claude-queue*.json"))):
            try:
                queue = json.loads(Path(qf).read_text(encoding="utf-8"))
                pending = [t for t in queue if t.get("status") == "pending"]
                # Determine session index from filename
                stem = Path(qf).stem  # "claude-queue" or "claude-queue-1"
                if stem == "claude-queue":
                    session_idx = 0
                else:
                    try:
                        session_idx = int(stem.rsplit("-", 1)[-1])
                    except ValueError:
                        session_idx = 0

                # Update pending count in session
                if session_idx < len(self.sessions):
                    old_count = self.sessions[session_idx].get("pending_count", 0)
                    self.sessions[session_idx]["pending_count"] = len(pending)
                    if len(pending) != old_count:
                        self.root.after(0, self._rebuild_session_rows)

                if not pending:
                    continue
                if session_idx >= len(self.sessions):
                    continue
                s = self.sessions[session_idx]
                if s.get("running") or self._bridge_stopping:
                    continue
                # Cooldown: don't auto-restart within 30s of manual exit
                exit_time = self._session_exit_time.get(session_idx, 0)
                if time.time() - exit_time < 30:
                    continue
                self._log(f"[看门狗] session {session_idx} 检测到 {len(pending)} 个待处理任务，自动启动 [{s['name']}]...")
                self.root.after(500, lambda idx=session_idx: self.start_claude(idx))
            except Exception:
                pass

    def _check_claude_health(self):
        """Auto-restart dead Claude sessions (user-stopped sessions excluded)."""
        if not self.bridge_running or self._bridge_stopping:
            return
        for i, s in enumerate(self.sessions):
            if s.get("running"):
                self._claude_restart_count.pop(i, None)
                continue
            if not s.get("bot_id"):
                continue
            if i in self._user_stopped:
                continue
            # Cooldown after exit
            exit_time = self._session_exit_time.get(i, 0)
            if time.time() - exit_time < 30:
                continue
            # Rate limiting
            now = time.time()
            last = self._claude_last_restart.get(i, 0)
            if now - last < 3600:
                continue
            count = self._claude_restart_count.get(i, 0)
            if count >= 3:
                continue
            self._claude_restart_count[i] = count + 1
            self._claude_last_restart[i] = now
            self._log(f"[看门狗] session {i} [{s['name']}] 异常退出，自动重启 (第 {count + 1}/3 次)...")
            self.root.after(500, lambda idx=i: self.start_claude(idx))

    # ── all ──

    def start_all(self, *args):
        if not self.bridge_running:
            self.start_bridge()
        for i in range(len(self.sessions)):
            if not self.sessions[i].get("running"):
                self.start_claude(i)

    def stop_all(self, *args):
        for i in range(len(self.sessions)):
            if self.sessions[i].get("running"):
                self.stop_claude(i)
        if self.bridge_running:
            self.stop_bridge()

    def _update_all_btn(self):
        claude_running = self._session_running_count() > 0
        all_running = self.bridge_running and all(s.get("running") for s in self.sessions) if self.sessions else self.bridge_running
        any_running = self.bridge_running or claude_running
        self.btn_all_start.configure(state="disabled" if (self.bridge_running and claude_running) else "normal")
        self.btn_all_stop.configure(state="normal" if any_running else "disabled")

        # Tray icon
        if all_running:
            self.tray.icon = self.icon_running
        else:
            self.tray.icon = self.icon_stopped
        # Update tooltip with pending count
        total_pending = sum(s.get("pending_count", 0) for s in self.sessions)
        if total_pending > 0:
            self.tray.title = f"{TITLE} ({total_pending} 待处理)"
        else:
            self.tray.title = TITLE

    # ── cleanup ──

    def _force_kill_bridge_children(self):
        """Kill all bridge child processes. Called on exit (atexit) even if Hub is killed externally."""
        for p in self.procs:
            try:
                if p.poll() is None:
                    subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                        capture_output=True, timeout=2,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            except Exception:
                pass
        # Clean stale pid file
        pid_file = BRIDGE_DATA_DIR / "consumer.pid"
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _cleanup_zombies():
        """Kill all python/pythonw processes related to this project (bridge, consumer, monitor, etc.)."""
        proj = str(PROJECT_DIR.resolve())
        try:
            subprocess.run(
                ["powershell", "-WindowStyle", "Hidden", "-Command",
                 # Kill python/pythonw processes whose command line mentions this project
                 "Get-Process python,pythonw -ErrorAction SilentlyContinue | " +
                 "ForEach-Object { $cmd = (Get-WmiObject Win32_Process -Filter 'ProcessId = ' + $_.Id).CommandLine; " +
                 f"if ($cmd -match [regex]::Escape('{proj}')) {{ Stop-Process -Id $_.Id -Force }} }}"],
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except Exception:
            pass
        # Clean all stale PID lock files
        pid_dir = PROJECT_DIR / "session-context" / "qq-bridge"
        try:
            import ctypes as _ct
            for f in list(pid_dir.glob("*.pid")):
                try:
                    old_pid = int(f.read_text().strip())
                    h = _ct.windll.kernel32.OpenProcess(0x0400, False, old_pid)
                    if h:
                        _ct.windll.kernel32.CloseHandle(h)
                    else:
                        f.unlink(missing_ok=True)
                except Exception:
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass
        # Clean stale heartbeat
        hb = pid_dir / "watchdog.hb"
        try:
            hb.unlink(missing_ok=True)
        except Exception:
            pass

    # ── stdout reader ──

    def _read_stdout(self, proc: subprocess.Popen, tag: str):
        try:
            for line in proc.stdout:
                self.log_queue.put(f"{tag} {line.rstrip()}")
        except Exception:
            pass

    def _read_monitor(self, proc: subprocess.Popen):
        try:
            for line in proc.stdout:
                text = line.rstrip()
                self.log_queue.put(f"[monitor] {text}")
                if "TASK_DETECTED" in text:
                    self.root.after(0, self._check_queue)
        except Exception:
            pass

    # ── logging ──

    def _log(self, text: str):
        self.log_queue.put(text)

    def _poll_lock(self):
        """Accept 'show' command from subsequent instances to bring window to front."""
        if self._lock_sock:
            try:
                conn, _addr = self._lock_sock.accept()
                data = conn.recv(4)
                if data == b"show":
                    self._show_window()
                conn.close()
            except Exception:
                pass
        self.root.after(500, self._poll_lock)

    def _poll_log(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_area.configure(state="normal")
            self.log_area.insert("end", msg + "\n")
            self.log_area.configure(state="disabled")
            self.log_area.see("end")
        self.root.after(200, self._poll_log)

    def _on_autostart_toggle(self):
        save_config({"auto_start": self.autostart_var.get()})

    def run(self):
        self.root.after(500, self._poll_lock)
        if self._force_auto or self.autostart_var.get():
            self.root.after(800, self.start_all)
        self.root.mainloop()


def _notify_already_running():
    """Show MessageBox when another instance is already running."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "Claude Hub 已在系统托盘中运行。\n\n请查看任务栏右下角托盘图标，\n右键 → 显示窗口 或 退出。",
            "Claude Hub",
            0x40 | 0x0  # MB_ICONINFORMATION | MB_OK
        )
    except Exception:
        pass


if __name__ == "__main__":
    lock_sock, is_first = ensure_single_instance()
    if not is_first:
        _notify_already_running()
        sys.exit(0)
    force_auto = "--auto-start" in sys.argv
    try:
        ui = HubUI(lock_sock, force_auto=force_auto)
        ui.run()
    except Exception:
        import traceback
        err_msg = f"Claude Hub 启动失败:\n\n{traceback.format_exc()}"
        try:
            (PROJECT_DIR / "hub-startup-error.log").write_text(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{err_msg}\n", encoding="utf-8")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, err_msg, "Claude Hub 错误", 0x10 | 0x0)
        except Exception:
            pass
        sys.exit(1)
