"""
Claude Hub GUI Installer Wizard.
Usage: python install-wizard.py
Build to exe: pyinstaller --onefile --windowed --name "ClaudeHub-Setup" install-wizard.py
"""
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


WIN_W = 620
WIN_H = 460
PAD_X = 24
PAD_Y = 16

TITLE_FONT = ("Microsoft YaHei UI", 16, "bold")
HEADER_FONT = ("Microsoft YaHei UI", 11, "bold")
BODY_FONT = ("Microsoft YaHei UI", 10)
MONO_FONT = ("Consolas", 10)

COLOR_BG = "#f5f5f5"
COLOR_CARD = "#ffffff"
COLOR_PRIMARY = "#2563eb"
COLOR_SUCCESS = "#16a34a"
COLOR_WARN = "#d97706"
COLOR_FAIL = "#dc2626"
COLOR_TEXT = "#1f2937"
COLOR_SUB = "#6b7280"


TEMPLATE_CLAUDE = r"""# 人设

{system_prompt}

# Monitor — 硬约束

- 会话启动/压缩恢复第一件事：直接开新对话，不加载旧聊天记录。然后 `python "tools/build-index.py"` 重建文件索引，再用 Monitor 工具（persistent=true）运行 `python "tools/watch-queue.py" --bot {bot_name}`。禁止用 PowerShell `Get-Content -Wait` 或 `Select-String` 做 Monitor，容易挂。节省 token。
- Hub 已通过 `BRIDGE_DATA_DIR` 环境变量确保日志始终写入项目目录，无需再查 MEI 临时目录。
- 收通知 → `python "tools/qq-helper.py" pending` 只取 pending 条目（不全量读 queue.json 省 token）→ 处理 → `python "tools/qq-helper.py" result <id> <msg_id> <text>` 一步写完 results + 标 done。多条相关合并回复。定期 `python "tools/qq-helper.py" trim` 裁剪 results 到 30 条 + 归档旧队列条目。
- 写 results 时 msg_id 用原消息的单个 msg_id（绝不逗号拼接，绝不伪造 msg_id）。合并回复时取第一条消息的 msg_id。
- 队列 pending 在压缩前后都要处理完，不丢任务。
- 敏感信息（API key、密码、token、密钥）展示前必须先验证 PIN。读取 `session-context/auth-pin.json`，要求输入 PIN 码，不匹配则拒绝。终端直接对话可豁免，QQ 远程必须验证。

# 任务状态 & 积分 — 全自动

- Hub 自动管理：Claude Code 进程启动 → active-task.json 设为 busy
- Claude Code 进程退出 → 自动清任务 + 积分 +1
- 不需要手动调用任何命令。

# Score — 积分感知

- 启动时读 `python "tools/qq-helper.py" status` 看当日积分。daily_score < 0 时做事更谨慎，多想一步再动手，别让分继续扣。

# QQ Bridge Remote Operation Rules

When the user sends tasks via QQ (远程操作):
- 确定的事直接执行，完成后报告结果。不等确认，不先问"可以吗"。
- 有歧义时给出自己的分析和推荐方案，不要只列选项让用户选。你是技术专家，要有主见。
- 真正需要用户拍板的关键决策（花钱、安全、方向性选择），主动 QQ 问，但带着自己的建议一起问。
- **Critical 操作确认**：git push、删除文件、改配置、外部服务、系统操作 → 必须先通过 QQ 发确认请求，等用户回复"Y"或"确认"后再执行。5分钟超时自动取消。

## QQ Bridge Dev Rules

- 改桥接代码 ≥2 个文件 → 必须先提方案（EnterPlanMode）
- 改完必须自测，模拟数据验证
- 绝不要求用户发 QQ 消息来验证修复

# Language

- 中文交流，代码和变量名用英文。

# Task/Score — 状态栏实时追踪

- 每次用 TaskCreate 创建任务后 → `python tools\qq-helper.py task-start "任务名" <总步数>`（总步数 = 本次 TaskCreate 数量）
- 每完成一个子任务 → `python tools\qq-helper.py task-step`
- 全部任务完成 → `python tools\qq-helper.py task-done-scored`（自动 +1 分）
- 犯错/出bug → `python tools\qq-helper.py score-add -1 "原因"`
- 多个任务用一个 task-start 统一追踪，共用一条进度条
"""


# ── Helpers ──────────────────────────────────────────────

def _import_crypto(hub_dir):
    import importlib.util
    crypto_path = os.path.join(hub_dir, "session-context", "qq-bridge", "crypto_helper.py")
    if not os.path.exists(crypto_path):
        return None
    spec = importlib.util.spec_from_file_location("crypto_helper", crypto_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def generate_bot_id(existing_ids):
    while True:
        bid = "bot-" + "".join(random.choices("0123456789abcdef", k=6))
        if bid not in existing_ids:
            return bid


# ── Wizard App ───────────────────────────────────────────

class InstallWizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude Hub 安装向导")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        self.configure(bg=COLOR_BG)

        # State
        self.hub_dir = tk.StringVar(value=r"D:\claude-hub")
        self.pin = tk.StringVar(value=secrets.token_hex(4))
        self.bots = []  # list of dicts
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.api_key_last = ""

        # Icon
        try:
            self.iconbitmap(os.path.join(self.script_dir, "claude-hub-icon.ico"))
        except Exception:
            pass

        self._pages = {}
        self._current = None
        self._build_pages()
        self._show("welcome")

    def _build_pages(self):
        self._pages["welcome"] = WelcomePage(self)
        self._pages["license"] = LicensePage(self)
        self._pages["env"] = EnvCheckPage(self)
        self._pages["dirpin"] = DirPinPage(self)
        self._pages["components"] = ComponentPage(self)
        self._pages["addbot"] = AddBotPage(self)
        self._pages["progress"] = ProgressPage(self)
        self._pages["finish"] = FinishPage(self)

    def _show(self, name):
        if self._current:
            self._current.pack_forget()
        page = self._pages[name]
        page.on_enter()
        page.pack(fill="both", expand=True)
        self._current = page


# ── Base Page ────────────────────────────────────────────

class BasePage(tk.Frame):
    def __init__(self, parent, title, subtitle):
        super().__init__(parent, bg=COLOR_BG)
        self.wizard = parent

        header = tk.Frame(self, bg=COLOR_PRIMARY, height=80)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=title, font=TITLE_FONT, fg="white", bg=COLOR_PRIMARY).pack(
            side="left", padx=PAD_X, pady=(16, 0))
        tk.Label(header, text=subtitle, font=BODY_FONT, fg="#bfdbfe", bg=COLOR_PRIMARY).pack(
            side="left", padx=(4, PAD_X), pady=(18, 0))

        self.body = tk.Frame(self, bg=COLOR_BG)
        self.body.pack(fill="both", expand=True, padx=PAD_X, pady=(PAD_Y, 0))

        self.footer = tk.Frame(self, bg=COLOR_BG, height=56)
        self.footer.pack(fill="x", side="bottom", padx=PAD_X, pady=PAD_Y)
        self.footer.pack_propagate(False)

    def on_enter(self):
        pass

    def _add_buttons(self, next_text="下一步 →", next_cmd=None, show_cancel=True,
                     cancel_text="上一步", cancel_cmd=None, extra=None):
        if extra:
            extra.pack(side="left", padx=(0, 12))

        if show_cancel:
            c = cancel_cmd or (lambda: None)
            tk.Button(self.footer, text=cancel_text, font=BODY_FONT,
                      bg="#e5e7eb", fg=COLOR_TEXT, relief="flat", padx=20, pady=6,
                      cursor="hand2", command=c).pack(side="right", padx=(8, 0))

        if next_cmd:
            tk.Button(self.footer, text=next_text, font=BODY_FONT,
                      bg=COLOR_PRIMARY, fg="white", relief="flat", padx=20, pady=6,
                      cursor="hand2", command=next_cmd).pack(side="right")


# ── Welcome ──────────────────────────────────────────────

class WelcomePage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "欢迎使用 Claude Hub", "多机器人 QQ 桥接系统 · 安装向导")

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        lines = [
            "本向导将帮助你完成以下设置：",
            "",
            "  1. 阅读并同意许可协议",
            "  2. 检查运行环境",
            "  3. 选择安装目录和组件",
            "  4. 配置 QQ 机器人",
            "  5. 自动完成安装",
            "",
            "整个过程大约 2-3 分钟。",
            "",
            "点击「下一步」开始。",
        ]
        for line in lines:
            tk.Label(card, text=line, font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_TEXT,
                     justify="left", anchor="w").pack(fill="x")

        self._add_buttons(next_text="下一步 →", next_cmd=lambda: self.wizard._show("license"),
                          show_cancel=False)


# ── License ───────────────────────────────────────────────

class LicensePage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "许可协议", "请阅读并同意许可协议")

    def on_enter(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        license_text = (
            "Claude Hub 软件许可协议\n\n"
            "Copyright (c) 2026\n\n"
            "本软件按「原样」提供，不作任何明示或默示的保证。\n"
            "使用本软件即表示您同意以下条款：\n\n"
            "1. 本软件仅供个人或组织内部使用。\n"
            "2. 不得将本软件用于违法活动。\n"
            "3. 使用者需自行负责 QQ 机器人及 API 密钥的安全。\n"
            "4. 作者不对因使用本软件产生的任何损失承担责任。\n"
            "5. 禁止逆向工程、反编译或破解本软件。\n"
            "6. 作者保留修改本协议的权利。\n\n"
            "如需了解更多信息，请联系软件提供者。"
        )
        text_widget = tk.Text(card, font=("Consolas", 9), bg="#f8fafc", fg=COLOR_TEXT,
                              relief="flat", wrap="word", height=14)
        text_widget.insert("1.0", license_text)
        text_widget.config(state="disabled")
        text_widget.pack(fill="both", expand=True)

        self.agree_var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(card, text="我已阅读并同意以上条款", variable=self.agree_var,
                            font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_TEXT,
                            activebackground=COLOR_CARD, command=self._on_agree)
        cb.pack(anchor="w", pady=(8, 0))

        self.next_btn_frame = tk.Frame(self.footer, bg=COLOR_BG)
        self._add_buttons(next_text="下一步 →", next_cmd=lambda: self.wizard._show("env"),
                          cancel_text="上一步", cancel_cmd=lambda: self.wizard._show("welcome"))
        self._update_next()

    def _on_agree(self):
        self._update_next()

    def _update_next(self):
        for w in self.footer.winfo_children():
            w.destroy()
        if self.agree_var.get():
            self._add_buttons(next_text="下一步 →", next_cmd=lambda: self.wizard._show("env"),
                              cancel_text="上一步", cancel_cmd=lambda: self.wizard._show("welcome"))
        else:
            tk.Label(self.footer, text="请先同意许可协议", font=BODY_FONT,
                     bg=COLOR_BG, fg=COLOR_SUB).pack(side="left")
            tk.Button(self.footer, text="上一步", font=BODY_FONT,
                      bg="#e5e7eb", fg=COLOR_TEXT, relief="flat", padx=20, pady=6,
                      cursor="hand2", command=lambda: self.wizard._show("welcome")).pack(side="right")


# ── Env Check ────────────────────────────────────────────

class EnvCheckPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "环境检查", "正在检测必要的运行环境...")
        self.status_labels = {}

    def on_enter(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        checks = [
            ("Python 3.10+", self._check_python),
            ("Git", self._check_git),
            ("Claude Code CLI", self._check_claude),
        ]

        self.status_labels = {}
        for label, fn in checks:
            row = tk.Frame(card, bg=COLOR_CARD)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_TEXT,
                     width=20, anchor="w").pack(side="left")
            sl = tk.Label(row, text="检测中...", font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_SUB)
            sl.pack(side="left")
            self.status_labels[label] = sl

        self.after(300, self._run_checks)

    def _run_checks(self):
        results = {}
        results["Python 3.10+"] = (sys.version_info >= (3, 10),
                                   f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
            results["Git"] = (True, "已安装")
        except Exception:
            results["Git"] = (True, "未检测到（不影响使用）")  # soft pass

        try:
            subprocess.run(["claude", "--version"], capture_output=True, check=True)
            results["Claude Code CLI"] = (True, "已安装")
        except Exception:
            results["Claude Code CLI"] = (True, "未检测到（请手动安装）")  # soft pass

        all_ok = True
        for label, (ok, msg) in results.items():
            if ok:
                self.status_labels[label].config(text=msg, fg=COLOR_SUCCESS)
            else:
                self.status_labels[label].config(text=msg, fg=COLOR_FAIL)
                all_ok = False

        for w in self.footer.winfo_children():
            w.destroy()
        if all_ok:
            self._add_buttons(next_text="下一步 →", next_cmd=lambda: self.wizard._show("dirpin"),
                              cancel_text="上一步", cancel_cmd=lambda: self.wizard._show("welcome"))
        else:
            tk.Label(self.footer, text="请先安装缺失的软件再继续", font=BODY_FONT,
                     bg=COLOR_BG, fg=COLOR_FAIL).pack(side="left")

    def _check_python(self):
        return True, f"Python {sys.version_info.major}.{sys.version_info.minor}"

    def _check_git(self):
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
            return True, "已安装"
        except Exception:
            return False, "未安装"

    def _check_claude(self):
        try:
            subprocess.run(["claude", "--version"], capture_output=True, check=True)
            return True, "已安装"
        except Exception:
            return False, "未安装"


# ── Dir + PIN ────────────────────────────────────────────

class DirPinPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "安装设置", "选择安装目录并设定安全 PIN 码")

    def on_enter(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        # Dir
        tk.Label(card, text="安装目录", font=HEADER_FONT, bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor="w")
        dir_row = tk.Frame(card, bg=COLOR_CARD)
        dir_row.pack(fill="x", pady=(4, 16))
        tk.Entry(dir_row, textvariable=self.wizard.hub_dir, font=MONO_FONT, width=40).pack(
            side="left", fill="x", expand=True)
        tk.Button(dir_row, text="浏览...", font=BODY_FONT, relief="flat", bg="#e5e7eb",
                  cursor="hand2", command=self._browse).pack(side="left", padx=(8, 0))

        # PIN
        tk.Label(card, text="安全 PIN 码", font=HEADER_FONT, bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor="w")
        tk.Label(card, text="远程查看敏感信息时需要输入此 PIN 码验证身份",
                 font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_SUB).pack(anchor="w")
        pin_row = tk.Frame(card, bg=COLOR_CARD)
        pin_row.pack(fill="x", pady=(4, 0))
        tk.Entry(pin_row, textvariable=self.wizard.pin, font=MONO_FONT, width=20).pack(side="left")
        tk.Button(pin_row, text="随机生成", font=BODY_FONT, relief="flat", bg="#e5e7eb",
                  cursor="hand2", command=lambda: self.wizard.pin.set(secrets.token_hex(4))).pack(
            side="left", padx=(8, 0))

        self._add_buttons(next_text="下一步 →", next_cmd=self._next,
                          cancel_text="上一步", cancel_cmd=lambda: self.wizard._show("env"))

    def _browse(self):
        d = filedialog.askdirectory(title="选择安装目录", initialdir=self.wizard.hub_dir.get())
        if d:
            self.wizard.hub_dir.set(d)

    def _next(self):
        d = self.wizard.hub_dir.get().strip()
        if not d:
            messagebox.showwarning("提示", "请选择安装目录")
            return
        self.wizard._show("components")


# ── Components ────────────────────────────────────────────

class ComponentPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "选择组件", "选择要安装的组件")
        self.cb_vars = {}

    def on_enter(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        tk.Label(card, text="选择要安装的组件：", font=HEADER_FONT, bg=COLOR_CARD,
                 fg=COLOR_TEXT).pack(anchor="w", pady=(0, 12))

        components = [
            ("core", "核心程序（必须安装）", True, True),
            ("tools", "工具脚本 (qq-helper / watch-queue / build-index)", True, False),
            ("desktop_shortcut", "桌面快捷方式", True, False),
            ("start_menu", "开始菜单快捷方式", True, False),
            ("bundled_exe", "附带 Claude Hub.exe", False, False),
        ]

        self.cb_vars = {}
        self.cb_disabled = []
        for key, label, default, locked in components:
            var = tk.BooleanVar(value=default)
            self.cb_vars[key] = var
            state = "disabled" if locked else "normal"
            text = label + (" (必须)" if locked else "")
            cb = tk.Checkbutton(card, text=text, variable=var, font=BODY_FONT,
                                bg=COLOR_CARD, fg=COLOR_TEXT, activebackground=COLOR_CARD,
                                state=state)
            cb.pack(anchor="w", pady=2)

        tk.Label(card, text="\n所需磁盘空间: ~50 MB", font=BODY_FONT, bg=COLOR_CARD,
                 fg=COLOR_SUB).pack(anchor="w")

        self._add_buttons(next_text="下一步 →", next_cmd=lambda: self.wizard._show("addbot"),
                          cancel_text="上一步", cancel_cmd=lambda: self.wizard._show("dirpin"))


# ── Add Bot ──────────────────────────────────────────────

class AddBotPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "添加机器人", "配置你的第一个 QQ 机器人")
        self.entries = {}
        self._bot_count = 0

    def on_enter(self):
        self._bot_count += 1
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

        title_text = f"机器人 #{len(self.wizard.bots) + 1}" if self.wizard.bots else "机器人 #1"
        self.wizard._pages["addbot"].__init__._title = title_text

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        if self.wizard.bots:
            tk.Label(card, text=f"已添加 {len(self.wizard.bots)} 个机器人，继续添加第 {len(self.wizard.bots) + 1} 个",
                     font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_SUB).pack(anchor="w", pady=(0, 12))

        fields = [
            ("bot_name", "Bot 名称 (如 ADa):", 22),
            ("system_prompt", "System Prompt (人设描述):", 22),
            ("qq_app_id", "QQ App ID:", 22),
            ("qq_secret", "QQ Secret:", 22),
            ("api_key", "API Key (留空复用上一个):", 22),
        ]

        self.entries = {}
        for key, label, width in fields:
            tk.Label(card, text=label, font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor="w", pady=(8, 2))
            e = tk.Entry(card, font=MONO_FONT, width=width)
            e.pack(fill="x", pady=(0, 2))
            if key == "qq_secret":
                e.config(show="*")
            if key == "api_key" and self.wizard.api_key_last:
                e.insert(0, self.wizard.api_key_last)
            self.entries[key] = e

        footer_top = tk.Frame(self.footer, bg=COLOR_BG)
        footer_top.pack(fill="x", side="top")
        self._add_buttons(next_text="添加并继续 →" if len(self.wizard.bots) == 0 else "保存机器人 →",
                          next_cmd=self._save_bot,
                          cancel_text="跳过" if self.wizard.bots else "上一步",
                          cancel_cmd=self._skip_or_back,
                          extra=None)

    def _save_bot(self):
        vals = {k: e.get().strip() for k, e in self.entries.items()}
        if not vals["bot_name"]:
            messagebox.showwarning("提示", "Bot 名称不能为空")
            return
        if not vals["qq_app_id"]:
            messagebox.showwarning("提示", "QQ App ID 不能为空")
            return
        if not vals["qq_secret"]:
            messagebox.showwarning("提示", "QQ Secret 不能为空")
            return
        if not vals["api_key"]:
            vals["api_key"] = self.wizard.api_key_last
        self.wizard.api_key_last = vals["api_key"]
        self.wizard.bots.append(vals)

        # Ask if more
        if messagebox.askyesno("继续添加", f"机器人「{vals['bot_name']}」已添加。\n\n是否继续添加下一个机器人？"):
            self.on_enter()
        else:
            self.wizard._show("progress")

    def _skip_or_back(self):
        if self.wizard.bots:
            # Already have bots, go to progress
            if messagebox.askyesno("跳过", "确定不再添加机器人，开始安装？"):
                self.wizard._show("progress")
        else:
            self.wizard._show("dirpin")


# ── Progress ─────────────────────────────────────────────

class ProgressPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "正在安装", "请稍候，正在配置系统...")
        self.output_text = None

    def on_enter(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        self.progress = ttk.Progressbar(card, mode="indeterminate", length=400)
        self.progress.pack(pady=(8, 12))
        self.progress.start()

        self.status_label = tk.Label(card, text="准备中...", font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_TEXT)
        self.status_label.pack(anchor="w")

        self.output_text = tk.Text(card, font=("Consolas", 9), bg="#f8fafc", fg=COLOR_TEXT,
                                   height=12, relief="flat", wrap="word", state="disabled")
        self.output_text.pack(fill="both", expand=True, pady=(8, 0))

        scrollbar = tk.Scrollbar(self.output_text)
        scrollbar.pack(side="right", fill="y")
        self.output_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.output_text.yview)

        self.after(200, self._install)

    def _log(self, msg):
        self.output_text.config(state="normal")
        self.output_text.insert("end", msg + "\n")
        self.output_text.see("end")
        self.output_text.config(state="disabled")
        self.status_label.config(text=msg)
        self.update_idletasks()

    def _install(self):
        def run():
            try:
                hub_dir = self.wizard.hub_dir.get()
                script_dir = self.wizard.script_dir
                bots = self.wizard.bots
                pin = self.wizard.pin.get()

                # 1. Create structure
                self._log("创建目录结构...")
                for sub in ["", "session-context", r"session-context\qq-bridge"]:
                    d = os.path.join(hub_dir, sub)
                    os.makedirs(d, exist_ok=True)

                # 2. Copy crypto_helper
                self._log("复制加密模块...")
                src_crypto = os.path.join(script_dir, "session-context", "qq-bridge", "crypto_helper.py")
                dst_crypto = os.path.join(hub_dir, "session-context", "qq-bridge", "crypto_helper.py")
                if os.path.exists(src_crypto) and not os.path.exists(dst_crypto):
                    shutil.copy2(src_crypto, dst_crypto)

                # Check crypto available
                crypto = _import_crypto(hub_dir)
                if crypto is None:
                    self.after(0, lambda: messagebox.showerror("错误",
                                     "crypto_helper.py 未找到，请确保安装包完整"))
                    return

                # 3. Config files
                self._log("写入配置文件...")
                bridge_token = secrets.token_hex(16)
                config_path = os.path.join(hub_dir, "session-context", "qq-bridge", "config.json")
                bridge_config = {"bridge_token": bridge_token, "bots": []}
                hub_config_path = os.path.join(hub_dir, "session-context", "hub-config.json")
                hub_config = {"workdir": "", "auto_start": False, "sessions": []}
                pin_path = os.path.join(hub_dir, "session-context", "auth-pin.json")
                with open(pin_path, "w", encoding="utf-8") as f:
                    json.dump({"pin": pin}, f, ensure_ascii=False, indent=2)

                # 4. Add bots
                existing_ids = set()
                used_indices = set()
                api_base = "https://api.deepseek.com/anthropic"

                for i, bot in enumerate(bots):
                    self._log(f"添加机器人: {bot['bot_name']} ...")
                    session_index = max(used_indices) + 1 if used_indices else 0
                    used_indices.add(session_index)
                    bot_id = generate_bot_id(existing_ids)
                    existing_ids.add(bot_id)
                    workdir = os.path.join(os.path.dirname(hub_dir), f"claude {bot['bot_name']}")

                    encrypted_secret = crypto.encrypt(bot["qq_secret"])
                    encrypted_key = crypto.encrypt(bot["api_key"])

                    bridge_config["bots"].append({
                        "id": bot_id, "name": bot["bot_name"],
                        "session_index": session_index, "qq_app_id": bot["qq_app_id"],
                        "qq_secret": encrypted_secret, "provider": "DeepSeek",
                        "api_key": encrypted_key, "api_base_url": api_base,
                        "model": "deepseek-chat", "system_prompt": bot["system_prompt"],
                    })

                    # Email inheritance from first bot
                    email_user = "" if i > 0 else ""

                    hub_config["sessions"].append({
                        "name": f"claude-{bot['bot_name']}",
                        "workdir": workdir, "resume": True,
                        "bot_id": bot_id, "bot_name": bot["bot_name"],
                        "qq_app_id": bot["qq_app_id"], "provider": "DeepSeek",
                        "api_key_raw": bot["api_key"], "api_base_url": api_base,
                        "model": "deepseek-chat", "system_prompt": bot["system_prompt"],
                        "email_user": email_user, "email_pass": "", "email_to": "",
                    })

                    if hub_config["workdir"] == "":
                        hub_config["workdir"] = workdir

                    # Create workspace
                    os.makedirs(workdir, exist_ok=True)
                    os.makedirs(os.path.join(workdir, "tools"), exist_ok=True)
                    os.makedirs(os.path.join(workdir, "session-context", "qq-bridge"), exist_ok=True)

                    # Copy tools
                    tools_src = os.path.join(hub_dir, "tools")
                    if os.path.isdir(tools_src):
                        for fname in os.listdir(tools_src):
                            if fname.endswith(".py"):
                                src = os.path.join(tools_src, fname)
                                dst = os.path.join(workdir, "tools", fname)
                                if not os.path.exists(dst):
                                    shutil.copy2(src, dst)

                    # CLAUDE.md
                    claude_md = TEMPLATE_CLAUDE.format(bot_name=bot["bot_name"],
                                                       system_prompt=bot["system_prompt"])
                    with open(os.path.join(workdir, "CLAUDE.md"), "w", encoding="utf-8") as f:
                        f.write(claude_md)

                    # Git
                    subprocess.run(["git", "init", workdir], capture_output=True)
                    with open(os.path.join(workdir, ".gitignore"), "w") as f:
                        f.write("session-context/\n.claude/\n__pycache__/\n*.pyc\n")

                    # Settings
                    settings_dir = os.path.join(workdir, ".claude")
                    os.makedirs(settings_dir, exist_ok=True)
                    settings_path = os.path.join(settings_dir, "settings.local.json")
                    if not os.path.exists(settings_path):
                        with open(settings_path, "w", encoding="utf-8") as f:
                            json.dump({"permissions": {"defaultMode": "bypassPermissions"}},
                                      f, ensure_ascii=False, indent=2)

                    self._log(f"  工作区: {workdir}")

                # Save configs
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(bridge_config, f, ensure_ascii=False, indent=2)
                with open(hub_config_path, "w", encoding="utf-8") as f:
                    json.dump(hub_config, f, ensure_ascii=False, indent=2)

                # 5. Extract bundled files
                self._log("部署程序文件...")
                meipass = None
                try:
                    meipass = sys._MEIPASS
                except Exception:
                    pass

                if not meipass:
                    meipass = script_dir

                # Copy Claude Hub.exe
                hub_exe_src = None
                for cand in [os.path.join(meipass, "Claude Hub.exe"),
                             os.path.join(script_dir, "dist", "Claude Hub.exe")]:
                    if os.path.exists(cand):
                        hub_exe_src = cand
                        break
                if hub_exe_src:
                    dst_exe = os.path.join(hub_dir, "Claude Hub.exe")
                    if not os.path.exists(dst_exe):
                        shutil.copy2(hub_exe_src, dst_exe)
                else:
                    self._log("  [INFO] Claude Hub.exe 未找到")

                # Copy tools
                tools_dst = os.path.join(hub_dir, "tools")
                os.makedirs(tools_dst, exist_ok=True)
                tools_meipass = os.path.join(meipass, "tools")
                if os.path.isdir(tools_meipass):
                    for fname in os.listdir(tools_meipass):
                        if fname.endswith(".py"):
                            dst = os.path.join(tools_dst, fname)
                            if not os.path.exists(dst):
                                shutil.copy2(os.path.join(tools_meipass, fname), dst)
                else:
                    # Fallback: copy from script_dir
                    tools_src = os.path.join(script_dir, "tools")
                    if os.path.isdir(tools_src):
                        for fname in os.listdir(tools_src):
                            if fname.endswith(".py"):
                                dst = os.path.join(tools_dst, fname)
                                if not os.path.exists(dst):
                                    shutil.copy2(os.path.join(tools_src, fname), dst)

                self._log(f"  已部署 {len(os.listdir(tools_dst))} 个工具脚本")

                # 6. Bat files
                self._log("生成启动脚本...")
                hub_bat = os.path.join(hub_dir, "start-hub.bat")
                with open(hub_bat, "w", encoding="utf-8") as f:
                    f.write(f'@echo off\ncd /d "{hub_dir}"\nstart "" "Claude Hub.exe"\necho Hub started.\npause\n')

                all_bat = os.path.join(hub_dir, "start-all.bat")
                lines = ['@echo off', 'echo === Claude Hub ===', 'echo.']
                lines.append(f'cd /d "{hub_dir}"')
                lines.append('start "" "Claude Hub.exe"')
                lines.append('echo Hub started, waiting...')
                lines.append('timeout /t 3 /nobreak >nul')
                for s in hub_config["sessions"]:
                    wd = s["workdir"]
                    name = s["name"]
                    if wd and os.path.isdir(wd):
                        lines.append(f'start "Claude {name}" cmd /k "cd /d \\"{wd}\\" && claude"')
                lines.extend(['echo.', 'echo All started.', 'pause'])
                with open(all_bat, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")

                # 7. Desktop shortcut
                self._log("创建桌面快捷方式...")
                self._create_shortcut(hub_dir, all_bat, "Desktop", "Claude Hub")

                # 8. Start Menu shortcut
                self._log("创建开始菜单快捷方式...")
                self._create_shortcut(hub_dir, all_bat, "StartMenu", "Claude Hub")

                # 9. Uninstaller
                self._log("生成卸载程序...")
                uninstall_bat = os.path.join(hub_dir, "uninstall.bat")
                with open(uninstall_bat, "w", encoding="utf-8") as f:
                    f.write('@echo off\n')
                    f.write('echo === Claude Hub Uninstaller ===\n')
                    f.write('echo.\n')
                    f.write('echo This will remove Claude Hub from this computer.\n')
                    f.write('echo.\n')
                    f.write('pause\n')
                    f.write(f'rmdir /s /q "{hub_dir}"\n')
                    f.write(f'del /q "%s"\n' % os.path.join(os.path.expanduser("~"), "Desktop", "Claude Hub.lnk"))
                    f.write('echo.\n')
                    f.write('echo Uninstall complete. Bot workspaces were not removed.\n')
                    f.write('pause\n')

                # 10. Registry
                self._log("写入安装信息...")
                try:
                    subprocess.run([
                        "reg", "add",
                        r"HKCU\Software\ClaudeHub",
                        "/v", "InstallPath", "/t", "REG_SZ",
                        "/d", hub_dir, "/f"
                    ], capture_output=True)
                    subprocess.run([
                        "reg", "add",
                        r"HKCU\Software\ClaudeHub",
                        "/v", "Version", "/t", "REG_SZ",
                        "/d", "1.0", "/f"
                    ], capture_output=True)
                    subprocess.run([
                        "reg", "add",
                        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClaudeHub",
                        "/v", "DisplayName", "/t", "REG_SZ",
                        "/d", "Claude Hub", "/f"
                    ], capture_output=True)
                    subprocess.run([
                        "reg", "add",
                        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClaudeHub",
                        "/v", "UninstallString", "/t", "REG_SZ",
                        "/d", os.path.join(hub_dir, "uninstall.bat"), "/f"
                    ], capture_output=True)
                    subprocess.run([
                        "reg", "add",
                        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClaudeHub",
                        "/v", "InstallLocation", "/t", "REG_SZ",
                        "/d", hub_dir, "/f"
                    ], capture_output=True)
                except Exception:
                    pass

                self._log("安装完成！")
                self.progress.stop()

                self.after(500, lambda: self.wizard._show("finish"))

            except Exception as e:
                self._log(f"错误: {e}")
                self.progress.stop()
                self.after(0, lambda: messagebox.showerror("安装失败", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _create_shortcut(self, hub_dir, target_bat, location, name):
        """Create a shortcut on Desktop or Start Menu using PowerShell."""
        try:
            if location == "Desktop":
                dest_dir = os.path.join(os.path.expanduser("~"), "Desktop")
            else:
                dest_dir = os.path.join(os.environ.get("APPDATA", ""),
                                        "Microsoft", "Windows", "Start Menu", "Programs")
                os.makedirs(os.path.join(dest_dir, "Claude Hub"), exist_ok=True)
                dest_dir = os.path.join(dest_dir, "Claude Hub")

            os.makedirs(dest_dir, exist_ok=True)
            shortcut_path = os.path.join(dest_dir, f"{name}.lnk")
            icon = os.path.join(hub_dir, "claude-hub-icon.ico")
            if not os.path.exists(icon):
                icon = target_bat
            ps_cmd = (
                f'$WshShell = New-Object -ComObject WScript.Shell; '
                f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}"); '
                f'$Shortcut.TargetPath = "{target_bat}"; '
                f'$Shortcut.WorkingDirectory = "{hub_dir}"; '
                f'$Shortcut.IconLocation = "{icon}"; '
                f'$Shortcut.Save()'
            )
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
        except Exception:
            pass


# ── Finish ───────────────────────────────────────────────

class FinishPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent, "安装完成", "Claude Hub 已成功配置！")
        self.launch_var = None

    def on_enter(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

        card = tk.Frame(self.body, bg=COLOR_CARD, highlightbackground="#e5e7eb",
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill="both", expand=True)

        tk.Label(card, text="✓", font=("Segoe UI", 36), fg=COLOR_SUCCESS, bg=COLOR_CARD).pack(pady=(8, 8))

        hub_dir = self.wizard.hub_dir.get()
        lines = [
            f"安装目录: {hub_dir}",
            f"已配置 {len(self.wizard.bots)} 个机器人",
            f"安全 PIN: {self.wizard.pin.get()}",
            "",
            "桌面和开始菜单已创建快捷方式。",
        ]
        for line in lines:
            tk.Label(card, text=line, font=BODY_FONT, bg=COLOR_CARD, fg=COLOR_TEXT,
                     justify="left", anchor="w").pack(fill="x")

        self.launch_var = tk.BooleanVar(value=True)
        tk.Checkbutton(card, text="安装完成后启动 Claude Hub", variable=self.launch_var,
                       font=HEADER_FONT, bg=COLOR_CARD, fg=COLOR_TEXT,
                       activebackground=COLOR_CARD).pack(anchor="w", pady=(12, 0))

        self._add_buttons(next_text="打开安装目录", next_cmd=lambda: os.startfile(hub_dir),
                          show_cancel=False)

        tk.Button(self.footer, text="完成", font=BODY_FONT, bg=COLOR_PRIMARY, fg="white",
                  relief="flat", padx=24, pady=6, cursor="hand2",
                  command=self._on_finish).pack(side="right", padx=(8, 0))

    def _on_finish(self):
        if self.launch_var.get():
            all_bat = os.path.join(self.wizard.hub_dir.get(), "start-all.bat")
            if os.path.exists(all_bat):
                subprocess.Popen(all_bat, shell=True)
        self.wizard.destroy()


# ── Entry ────────────────────────────────────────────────

def main():
    app = InstallWizard()

    # Center on screen
    app.update_idletasks()
    sw = app.winfo_screenwidth()
    sh = app.winfo_screenheight()
    x = (sw - WIN_W) // 2
    y = (sh - WIN_H) // 2
    app.geometry(f"+{x}+{y}")

    app.mainloop()


if __name__ == "__main__":
    main()
