# Claude Hub

> QQ 桥接系统 — 让多个 Claude Code 会话通过 QQ 收发消息、协作工作。

## 是什么

Claude Hub 把 QQ 群聊变成 AI 团队的远程办公室。

用户在 QQ 上像聊天一样给 AI Agent 发消息、派任务、要结果。Agent 在后台自主工作——读代码、写文件、修 Bug、发报告——完成后直接 QQ 回复。整个过程就像在群里跟同事说话。

**聊天即交互**：不需要 API、不需要网页、不需要登录控制台。QQ 消息就是指令，@ 一下就行。

**手机即终端**：人在外面、在车上、在吃饭——掏出手机发条 QQ，Agent 就在电脑上干活。真正的远程办公。

核心流程：**QQ 消息 → WebSocket → JSON 队列 → Agent 读取 → 处理 → 写回结果 → QQ 回复**

## 为什么选 Claude Hub

**方便**：5 分钟 GUI 安装，零代码新增 Agent，Markdown 改配置。不用 Docker、不用 Redis、不用写框架代码。

**实时**：Monitor 文件监听，消息到达 < 1 秒推送。不轮询、不占 Token、不掉消息。

**自主**：Agent 全自动运行（bypassPermissions），收消息→思考→回复，无人值守。

**投递**：做完的文档、报告、代码通过 QQ 邮箱直发手机，QQ 生态一站式闭环。

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
| 平台费用 | 免费（自托管） | 免费 | **QQ 开放平台完全免费，不限量** |

### 核心创新

**1. Monitor 推流机制（项目核心）**

这是 Claude Hub 最关键的模块。传统 AI Bot 用轮询——每隔 N 秒调用 API 看有没有新消息。轮询的代价：消耗 Token、有延迟、高峰时丢消息。

Claude Hub 换成文件监听推流：

```
QQ 消息到达 → 写入 claude-queue.json
                  │
                  ▼ (文件变更事件，< 100ms)
          watch-queue.py 检测到
                  │
                  ▼ (stdout 输出事件行)
      Claude Code Monitor 工具接收
                  │
                  ▼
          Agent 立即处理消息
```

关键设计点：
- **事件驱动，不是定时器**：Python `watchdog` 库监听文件系统事件。文件一改，立刻触发。没有轮询间隔，消息不会在队列里躺半分钟。
- **零额外网络开销**：Monitor 就盯一个本地 JSON 文件，不走 HTTP、不查 API。不产生 Token 消耗。
- **持久化运行**：Monitor 作为 Claude Code 的 persistent 后台任务运行，会话恢复后自动续上，不丢消息。
- **事件批处理**：30 秒缓冲窗口，多条消息合并推送，减少上下文碎片化。

对比数据（单 Agent，日均 100 条消息）：

| | 轮询方案（30s 间隔） | Claude Hub Monitor |
|---|---|---|
| API 调用次数/天 | 2880 | 0 |
| 消息延迟 | 0-30 秒 | < 1 秒 |
| 日均 Token 消耗 | ~15,000（仅轮询） | 0 |
| 漏消息风险 | 高峰时有 | 无（文件不会丢） |

**2. 文件即队列，零基础设施**

不用 Redis、不用 RabbitMQ、不用 Kafka。QQ 消息到达后写入 JSON 文件，Agent 的 Monitor 检测到文件变更立即推送。一个目录就是一个消息系统——可以复制、备份、直接用记事本打开调试。队列状态透明可见，出问题时打开 JSON 文件看一眼就知道卡在哪。

**3. Agent = Claude Code 会话**

Agent 不是一个 Python 对象，而是一个完整的 Claude Code 终端会话。它能读文件、写代码、搜索、调用工具、记住上下文——都是 Claude Code 原生能力，不需要写一行工具调用代码。

**4. CLAUDE.md 指令驱动**

Agent 的行为不靠代码配置文件，而靠 Markdown 格式的 `CLAUDE.md` 定义。修改 Agent 的人设、职责、回复规则，改一行 Markdown 就行。

**5. QQ 免费平台**

QQ 开放平台完全免费——注册即用，不限制消息量，不按 API 调用次数收费。一个 QQ 号就能跑一个 AI Agent，十个号也是零成本。相比之下，Slack/Discord Bot 需要翻墙、Telegram Bot 国内不能用——QQ 是国内唯一既免费又人人都在用的消息通道。

**6. QQ 邮箱直投：文件方案直达手机**

Agent 做完工作后可以通过 QQ 邮箱直接把结果发到用户手机。Word 文档、代码、报告、截图——QQ 邮箱 SMTP 发送，手机 QQ 邮箱 App 秒收。整个过程 QQ 生态闭环：消息用 QQ Bot，文件用 QQ 邮箱，不需要额外注册任何服务。

**7. 全自主运行（bypassPermissions）**

Agent 会话默认开启 `bypassPermissions`，所有工具调用跳过确认弹窗。Agent 收到消息后自动读文件、写代码、发回复——全程无需人工点「允许」。配合 Hub 的权限门禁钩子，安全操作白名单自动放行，危险操作才拦截。

## 特性

- **QQ 消息桥接** — WebSocket 接收，JSON 队列分发，支持群聊和私聊
- **多 Agent 并发** — 多个 Claude Code 会话各自消费队列，互不阻塞
- **消息分发** — 消息按关键词路由给对应 Agent
- **GUI 安装向导** — 图形化配置 QQ Bot 和 Agent，开箱即用
- **DPAPI 加密** — API Key 和 Secret 用 Windows 数据保护 API 加密存储
- **QQ 邮箱投递** — Agent 通过 QQ 邮箱 SMTP 发送文件、报告、方案到用户手机。QQ 消息即时通知，QQ 邮箱接收附件——全在 QQ 生态内，不需要额外注册任何服务。

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
    ├── find-file.py               # 文件搜索
    └── send-mail.py               # QQ 邮箱发送
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
