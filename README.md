# Claude Hub

> QQ 桥接系统 — 让多个 Claude Code 会话通过 QQ 收发消息、协作工作。

## 是什么

Claude Hub 是一套 Python 脚本，把 QQ Bot 的消息转成文件队列，供 Claude Code Agent 读取处理。每个 Agent 绑定一个 QQ Bot，通过共享队列收发消息。

核心流程：**QQ 消息 → WebSocket → JSON 队列 → Agent 读取 → 处理 → 写回结果 → QQ 回复**

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
