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

# 人设

{system_prompt}
