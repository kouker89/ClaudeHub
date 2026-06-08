"""
Claude Hub one-click installer.
Usage: python install-hub.py
"""
import json
import os
import random
import secrets
import shutil
import subprocess
import sys


HUB_DEFAULT = r"D:\claude-hub"

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


def _import_crypto(hub_dir):
    import importlib.util
    crypto_path = os.path.join(hub_dir, "session-context", "qq-bridge", "crypto_helper.py")
    spec = importlib.util.spec_from_file_location("crypto_helper", crypto_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def generate_bot_id(existing_ids):
    while True:
        bid = "bot-" + "".join(random.choices("0123456789abcdef", k=6))
        if bid not in existing_ids:
            return bid


def check_prerequisites():
    print("=== 检查环境 ===\n")

    ok = True

    # Python
    py_ver = sys.version_info
    print(f"  Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}  ", end="")
    if py_ver >= (3, 10):
        print("[OK]")
    else:
        print("[FAIL] 需要 Python 3.10+")
        ok = False

    # Git
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        print("  Git                             [OK]")
    except Exception:
        print("  Git                             [WARN] 未检测到，工作区将无法使用版本控制")

    # Node / Claude Code
    try:
        subprocess.run(["claude", "--version"], capture_output=True, check=True)
        print("  Claude Code CLI                 [OK]")
    except Exception:
        print("  Claude Code CLI                 [WARN] 未检测到，请确保已安装: npm install -g @anthropic-ai/claude-code")

    print("")
    return ok


def init_hub_structure(hub_dir):
    print("=== 初始化 Hub 目录 ===\n")

    dirs = [
        hub_dir,
        os.path.join(hub_dir, "session-context"),
        os.path.join(hub_dir, "session-context", "qq-bridge"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        print(f"  + {d}")

    print("")
    return True


def copy_essentials(hub_dir, script_dir):
    print("=== 复制必要文件 ===\n")

    src_crypto = os.path.join(script_dir, "session-context", "qq-bridge", "crypto_helper.py")
    dst_crypto = os.path.join(hub_dir, "session-context", "qq-bridge", "crypto_helper.py")

    if not os.path.exists(dst_crypto):
        if os.path.exists(src_crypto):
            shutil.copy2(src_crypto, dst_crypto)
            print(f"  crypto_helper.py -> qq-bridge/")
        else:
            print("  [WARN] crypto_helper.py 未找到，请手动放置")
    else:
        print("  crypto_helper.py 已存在，跳过")

    print("")
    return True


def init_config(hub_dir):
    print("=== 初始化配置文件 ===\n")

    config_path = os.path.join(hub_dir, "session-context", "qq-bridge", "config.json")
    hub_config_path = os.path.join(hub_dir, "session-context", "hub-config.json")
    pin_path = os.path.join(hub_dir, "session-context", "auth-pin.json")

    # config.json
    if not os.path.exists(config_path):
        bridge_token = secrets.token_hex(16)
        config = {
            "bridge_token": bridge_token,
            "bots": []
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"  config.json 已创建（桥接令牌: {bridge_token}）")
    else:
        print("  config.json 已存在，跳过")

    # hub-config.json
    if not os.path.exists(hub_config_path):
        hub_config = {
            "workdir": "",
            "auto_start": False,
            "sessions": []
        }
        with open(hub_config_path, "w", encoding="utf-8") as f:
            json.dump(hub_config, f, ensure_ascii=False, indent=2)
        print("  hub-config.json 已创建")
    else:
        print("  hub-config.json 已存在，跳过")

    # auth-pin.json
    if not os.path.exists(pin_path):
        pin = input("\n设定 PIN 码（用于远程查看敏感信息时的身份验证）: ").strip()
        if not pin:
            pin = secrets.token_hex(4)
            print(f"  未输入，随机生成 PIN: {pin}")
        with open(pin_path, "w", encoding="utf-8") as f:
            json.dump({"pin": pin}, f, ensure_ascii=False, indent=2)
        print(f"  auth-pin.json 已创建")

    print("")
    return True


def add_bot(hub_dir, api_key_reuse=None):
    print("--- 添加机器人 ---\n")

    config_path = os.path.join(hub_dir, "session-context", "qq-bridge", "config.json")
    hub_config_path = os.path.join(hub_dir, "session-context", "hub-config.json")

    with open(config_path, "r", encoding="utf-8") as f:
        bridge_config = json.load(f)
    with open(hub_config_path, "r", encoding="utf-8") as f:
        hub_config = json.load(f)

    # Collect input
    bot_name = input("  Bot 名称 (如 ADa): ").strip()
    if not bot_name:
        print("  [SKIP] 名称为空，取消")
        return None

    system_prompt = input("  System Prompt (人设描述): ").strip()
    if not system_prompt:
        print("  [SKIP] 人设不能为空")
        return None

    qq_app_id = input("  QQ App ID: ").strip()
    if not qq_app_id:
        print("  [SKIP] QQ App ID 不能为空")
        return None

    qq_secret = input("  QQ Secret: ").strip()
    if not qq_secret:
        print("  [SKIP] QQ Secret 不能为空")
        return None

    provider = input("  Provider [DeepSeek]: ").strip() or "DeepSeek"
    model = input("  Model [deepseek-chat]: ").strip() or "deepseek-chat"
    api_base = "https://api.deepseek.com/anthropic"

    api_key = input("  API Key (留空复用已输入的): ").strip()
    if not api_key:
        api_key = api_key_reuse or ""

    # Session index
    existing_bots = bridge_config.get("bots", [])
    used_indices = {b.get("session_index", 0) for b in existing_bots}
    session_index = max(used_indices) + 1 if used_indices else 0

    # Bot ID
    existing_ids = {b["id"] for b in existing_bots}
    bot_id = generate_bot_id(existing_ids)

    # Workdir
    workdir = os.path.join(os.path.dirname(hub_dir), f"claude {bot_name}")

    # Encrypt secrets
    crypto = _import_crypto(hub_dir)
    encrypted_secret = crypto.encrypt(qq_secret)
    encrypted_key = crypto.encrypt(api_key)
    print(f"  [OK] 密钥已加密 (DPAPI)")

    # Write bridge config
    bridge_config.setdefault("bots", []).append({
        "id": bot_id,
        "name": bot_name,
        "session_index": session_index,
        "qq_app_id": qq_app_id,
        "qq_secret": encrypted_secret,
        "provider": provider,
        "api_key": encrypted_key,
        "api_base_url": api_base,
        "model": model,
        "system_prompt": system_prompt,
    })
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(bridge_config, f, ensure_ascii=False, indent=2)
    print(f"  [OK] bot [{bot_id}] -> config.json")

    # Write hub config
    session_name = f"claude-{bot_name}"
    first_session = hub_config.get("sessions", [None])[0]
    hub_config.setdefault("sessions", []).append({
        "name": session_name,
        "workdir": workdir,
        "resume": True,
        "bot_id": bot_id,
        "bot_name": bot_name,
        "qq_app_id": qq_app_id,
        "provider": provider,
        "api_key_raw": api_key,
        "api_base_url": api_base,
        "model": model,
        "system_prompt": system_prompt,
        "email_user": first_session.get("email_user", "") if first_session else "",
        "email_pass": first_session.get("email_pass", "") if first_session else "",
        "email_to": first_session.get("email_to", "") if first_session else "",
    })
    with open(hub_config_path, "w", encoding="utf-8") as f:
        json.dump(hub_config, f, ensure_ascii=False, indent=2)
    print(f"  [OK] session [{session_name}] -> hub-config.json")

    # Update hub workdir to first bot's workspace
    if hub_config["workdir"] == "":
        hub_config["workdir"] = workdir
        with open(hub_config_path, "w", encoding="utf-8") as f:
            json.dump(hub_config, f, ensure_ascii=False, indent=2)

    # Create workspace
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.join(workdir, "tools"), exist_ok=True)
    os.makedirs(os.path.join(workdir, "session-context", "qq-bridge"), exist_ok=True)

    # Copy tools from hub tools dir
    tools_src = os.path.join(hub_dir, "tools")
    if os.path.isdir(tools_src):
        for fname in os.listdir(tools_src):
            if fname.endswith(".py"):
                src = os.path.join(tools_src, fname)
                dst = os.path.join(workdir, "tools", fname)
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)
        print(f"  [OK] 工具脚本已复制")

    # Write CLAUDE.md
    claude_md = TEMPLATE_CLAUDE.format(bot_name=bot_name, system_prompt=system_prompt)
    with open(os.path.join(workdir, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(claude_md)
    print(f"  [OK] CLAUDE.md 已生成")

    # Init git
    subprocess.run(["git", "init", workdir], capture_output=True)
    with open(os.path.join(workdir, ".gitignore"), "w") as f:
        f.write("session-context/\n.claude/\n__pycache__/\n*.pyc\n")
    print(f"  [OK] Git 仓库已初始化")

    # Settings
    settings_dir = os.path.join(workdir, ".claude")
    os.makedirs(settings_dir, exist_ok=True)
    settings_path = os.path.join(settings_dir, "settings.local.json")
    if not os.path.exists(settings_path):
        settings = {"permissions": {"defaultMode": "bypassPermissions"}}
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        print(f"  [OK] .claude/settings.local.json 已创建")

    print(f"\n  === 机器人添加完成 ===")
    print(f"  Bot:      {bot_name}")
    print(f"  Bot ID:   {bot_id}")
    print(f"  Session:  {session_index}")
    print(f"  工作目录: {workdir}\n")

    return bot_name


def generate_bat(hub_dir):
    print("=== 生成启动脚本 ===\n")

    # start-hub.bat
    hub_bat = os.path.join(hub_dir, "start-hub.bat")
    if not os.path.exists(hub_bat):
        exe_path = os.path.join(hub_dir, "dist", "Claude Hub.exe")
        if not os.path.exists(exe_path):
            exe_path = os.path.join(hub_dir, "session-context", "qq-bridge", "dist", "Claude Hub.exe")

        content = f'@echo off\ncd /d "{hub_dir}"\nstart "" "Claude Hub.exe"\necho Hub started.\n'
        # Use generic name if exe not found
        if not os.path.exists(exe_path):
            content = f'@echo off\ncd /d "{hub_dir}"\necho Please start Claude Hub.exe manually.\npause\n'

        with open(hub_bat, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  start-hub.bat  -> 启动 Hub 主界面")
    else:
        print("  start-hub.bat 已存在，跳过")

    # start-all.bat (starts hub + all bots)
    all_bat = os.path.join(hub_dir, "start-all.bat")
    if not os.path.exists(all_bat):
        hub_config_path = os.path.join(hub_dir, "session-context", "hub-config.json")
        lines = ['@echo off', 'echo === Claude Hub 一键启动 ===', 'echo.']
        lines.append(f'cd /d "{hub_dir}"')
        lines.append('start "" "Claude Hub.exe"')
        lines.append('echo Hub 已启动，等待 3 秒...')
        lines.append('timeout /t 3 /nobreak >nul')

        if os.path.exists(hub_config_path):
            with open(hub_config_path, "r", encoding="utf-8") as f:
                hub_config = json.load(f)
            for s in hub_config.get("sessions", []):
                wd = s.get("workdir", "")
                name = s.get("name", "")
                if wd and os.path.isdir(wd):
                    lines.append(f'start "Claude {name}" cmd /k "cd /d \\"{wd}\\" && claude"')
                    lines.append(f'echo Claude {name} 已启动')

        lines.append('echo.')
        lines.append('echo 全部启动完成。')
        lines.append('pause')
        with open(all_bat, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"  start-all.bat  -> 启动 Hub + 全部机器人")
    else:
        print("  start-all.bat 已存在，跳过")

    print("")


def main():
    print("=" * 60)
    print("       Claude Hub 一键安装")
    print("=" * 60)
    print()

    # Determine script location
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Detect mode: if hub-config.json exists with sessions, we're adding bots
    hub_config_path = os.path.join(script_dir, "session-context", "hub-config.json")
    is_fresh = True
    if os.path.exists(hub_config_path):
        with open(hub_config_path, "r", encoding="utf-8") as f:
            hc = json.load(f)
        if hc.get("sessions"):
            print("检测到已有安装。")
            print("[1] 添加新机器人")
            print("[2] 全新安装（将覆盖配置文件）")
            choice = input("选择 [1/2]: ").strip()
            if choice == "1":
                is_fresh = False
                hub_dir = script_dir

    if is_fresh:
        hub_dir = input(f"Hub 安装目录 [{HUB_DEFAULT}]: ").strip() or HUB_DEFAULT

        if os.path.exists(hub_dir):
            ans = input(f"目录 {hub_dir} 已存在，是否继续？(y/N): ").strip().lower()
            if ans != "y":
                print("已取消。")
                return

        # Run fresh install
        print()
        if not check_prerequisites():
            ans = input("环境检查有问题，是否继续？(y/N): ").strip().lower()
            if ans != "y":
                return

        init_hub_structure(hub_dir)
        copy_essentials(hub_dir, script_dir)
        init_config(hub_dir)

    else:
        hub_dir = script_dir

    # Add bots loop
    api_key_reuse = None
    while True:
        result = add_bot(hub_dir, api_key_reuse)
        if result and not api_key_reuse:
            # read back the key for reuse
            with open(os.path.join(hub_dir, "session-context", "hub-config.json"), "r", encoding="utf-8") as f:
                hc = json.load(f)
            sessions = hc.get("sessions", [])
            if sessions:
                api_key_reuse = sessions[-1].get("api_key_raw", "")

        more = input("是否继续添加机器人？(y/N): ").strip().lower()
        if more != "y":
            break

    # Generate bat scripts
    generate_bat(hub_dir)

    # Summary
    print("=" * 60)
    print("  安装完成！")
    print("=" * 60)
    print()
    print(f"  Hub 目录: {hub_dir}")
    print(f"  启动 Hub: {hub_dir}\\start-hub.bat")
    print(f"  一键启动: {hub_dir}\\start-all.bat")
    print()
    print("  下一步:")
    print("  1. 将 Claude Hub.exe 放到 Hub 目录下")
    print("  2. 运行 start-all.bat 启动全部服务")
    print("  3. 在 QQ 上给机器人发消息测试")
    print()


if __name__ == "__main__":
    main()
