# Claude Hub

> QQ 桥接系统 — 让多个 Claude Code 会话通过 QQ 收发消息、协作工作。

## 是什么

Claude Hub 是一套 Python 脚本，把 QQ Bot 的消息转成文件队列，供 Claude Code Agent 读取处理。每个 Agent 绑定一个 QQ Bot，通过共享队列收发消息。

核心流程：**QQ 消息 → WebSocket → JSON 队列 → Agent 读取 → 处理 → 写回结果 → QQ 回复**

## 为什么选 Claude Hub

**方便**：5 分钟 GUI 安装，零代码新增 Agent，Markdown 改配置。不用 Docker、不用 Redis、不用写框架代码。

**实时**：Monitor 文件监听，消息到达 < 1 秒推送。不轮询、不占 Token、不掉消息。

**自主**：Agent 全自动运行（bypassPermissions），收消息→思考→回复，无人值守。

### 与其他方案的不同

市面上的 AI Agent 框架（LangChain、CrewAI、AutoGen 等）走的都是「框架定义 Agent」的路子——用代码定义 Agent 的行为、记忆、工具调用。Claude Hub 换了一个思路：**Agent 就是一个 Claude Code 会话**，框架只负责把消息送进去、把结果送出来。

| | LangChain / CrewAI | Discord Bot 方案 | **Claude Hub** |
|---|---|---|---|
| Agent 形态 | Python 脚本 | Bot 回调函数 | Claude Code 原生会话 |
| Agent 能力 | 框架限定的 tool | 预设回复逻辑 | Claude 全能力（读文件/写代码/搜索/推理） |
| 消息通道 | 无内置 | Discord | **QQ（国内用户零门槛）** |
| 消息队列 | 无 / Redis | 无 | **文件队列（零依赖，即插即用）** |
| 事件推送 | 轮询 | WebSocket 回调 | **Monitor 文件监听推送** |
| 部署 | pip install + 写代码 | 搭服务器 + 写 handler | **GUI 安装向导，5 分钟上线** |
| 密钥存储 | .env 明文 | 环境变量 | **DPAPI 加密（Windows 原生）** |
| 多 Agent | 需手动编排 | 单 Bot | **CLAUDE.md 指令驱动，自动协作** |

### 核心创新

**1. 文件即队列，零基础设施**

不用 Redis、不用 RabbitMQ、不用 Kafka。QQ 消息到达后写入 JSON 文件，Agent 的 Monitor 检测到文件变更立即推送。一个目录就是一个消息系统，可以复制、备份、直接用记事本打开调试。

**2. Monitor 推流，不是轮询**

每个 Agent 有一个轻量 Monitor（`watch-queue.py`），监听文件变更事件。消息一到文件，Monitor 立刻推给 Claude Code，延迟 < 1 秒。不像传统方案需要定时轮询 API，省 Token 也省时间。

**3. Agent = Claude Code 会话**

Agent 不是一个 Python 对象，而是一个完整的 Claude Code 终端会话。它能读文件、写代码、搜索、调用工具、记住上下文——都是 Claude Code 原生能力，不需要写一行工具调用代码。

**4. CLAUDE.md 指令驱动**

Agent 的行为不靠代码配置文件，而靠 Markdown 格式的 `CLAUDE.md` 定义。修改 Agent 的人设、职责、回复规则，改一行 Markdown 就行，不需要改 Python 代码。

**5. QQ 原生集成**

国内用户用 QQ 发消息就像聊天一样。不需要装 Discord、不需要翻墙、不需要学新工具。一个 QQ 号 = 一个 AI Agent。

**6. 全自主运行（bypassPermissions）**

Agent 会话默认开启 `bypassPermissions`，所有工具调用跳过确认弹窗。Agent 收到消息后自动读文件、写代码、发回复——全程无需人工点「允许」。配合 Hub 的权限门禁钩子，安全操作白名单自动放行，危险操作才拦截。

## 特性

- **QQ 消息桥接** — WebSocket 接收，JSON 队列分发，支持群聊和私聊
- **多 Agent 并发** — 多个 Claude Code 会话各自消费队列，互不阻塞
- **消息分发** — 消息按关键词路由给对应 Agent
- **GUI 安装向导** — 图形化配置 QQ Bot 和 Agent，开箱即用
- **DPAPI 加密** — API Key 和 Secret 用 Windows 数据保护 API 加密存储
- **Monitor 推流** — 文件变更实时推送，Agent 秒级响应

## 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | |
| Git | 任意 | |
| Claude Code CLI | 最新 | `npm install -g @anthropic-ai/claude-code` |
| QQ Bot | — | [QQ 开放平台](https://q.qq.com) 注册，获取 AppID + Secret |
| Windows | 10/11 | DPAPI 依赖 |

## 快速开始

### 1. 安装

运行 `install-wizard.py`（GUI）或 `install-hub.py`（命令行），按提示配置。

> 或下载 Releases 里的 `ClaudeHub-Installer.zip`，运行打包好的 exe。

### 2. 启动 Hub

运行 `claude-hub-ui.py` 或打包的 `Claude Hub.exe`，启动系统托盘管理。

### 3. 启动 Agent

在每个 Agent 工作区打开 Claude Code，会按 CLAUDE.md 自动建索引、启 Monitor、接消息。

### 4. 测试

QQ 上给配置好的机器人发消息，确认回复正常。

## 项目结构

```
claude-hub/
├── install-wizard.py              # GUI 安装向导
├── install-hub.py                 # CLI 安装脚本
├── claude-hub-ui.py               # Hub 桌面管理（托盘 + 状态）
├── ClaudeHub-Setup.spec           # PyInstaller 打包配置
├── claude-hub-icon.ico            # 应用图标
├── requirements.txt               # Python 依赖
├── LICENSE                        # MIT
├── CLAUDE-DEPLOY.md               # 自动化部署指南
├── session-context/
│   ├── qq-bridge/                 # QQ 桥接核心
│   │   ├── qq-bridge.py           # WebSocket 收发
│   │   ├── task-consumer.py       # 任务消费
│   │   ├── monitor.py             # 队列监控
│   │   ├── crypto_helper.py       # DPAPI 加解密
│   │   └── start.bat              # 一键启动
│   ├── hook-session-start.py      # Claude Code 启动钩子
│   ├── hook-session-stop.py       # Claude Code 退出钩子
│   ├── show-score.ps1             # 终端状态栏
│   └── claude-md-template.md      # Agent CLAUDE.md 模板
└── tools/
    ├── qq-helper.py               # 队列读写、状态查询
    ├── watch-queue.py             # Monitor 监视线程
    ├── build-index.py             # 文件索引
    └── find-file.py               # 文件搜索
```

## 常见问题

**Q: 为什么只支持 Windows？**
A: 密钥加密用了 Windows DPAPI，跨平台需要换方案。

**Q: Token 消耗怎么控制？**
A: 每个 Agent 独立积分，减少不必要的 Monitor 轮询。

**Q: 怎么添加新 Agent？**
A: 运行 `python install-hub.py` 添加机器人，或 GUI 向导里操作。

## 许可

MIT License — 详见 [LICENSE](LICENSE)
