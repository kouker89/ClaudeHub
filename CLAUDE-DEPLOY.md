# Claude Hub 自动化部署指南

> 把这份文档丢给 Claude Code，它会按步骤帮你完成全部部署。

---

## 你的任务

帮我完成 Claude Hub 多机器人 QQ 桥接系统的全部部署。按以下步骤逐一执行，不要跳过。

---

## 第一步：环境检查

运行以下检查，确认环境就绪：

```bash
python --version   # 需要 3.10+
git --version      # 任意版本
claude --version   # Claude Code CLI
```

如果有缺失，先安装对应软件再继续。

---

## 第二步：运行安装向导

找到 `ClaudeHub-Setup.exe`（或运行 `python install-wizard.py`），执行图形化安装向导。

如果你用的是命令行脚本，运行：

```bash
python install-hub.py
```

按提示输入：
- 安装目录（默认 D:\claude-hub）
- PIN 码（用于安全验证）
- 机器人的 QQ App ID 和 Secret
- DeepSeek API Key
- 机器人名称和人设描述

---

## 第三步：验证安装结果

安装完成后，检查以下文件是否存在：

```bash
ls D:\claude-hub\Claude Hub.exe
ls D:\claude-hub\tools\qq-helper.py
ls D:\claude-hub\tools\watch-queue.py
ls D:\claude-hub\session-context\qq-bridge\config.json
ls D:\claude-hub\session-context\hub-config.json
```

---

## 第四步：在每个机器人工作区启动 Claude Code

读取 hub-config.json 获取所有机器人工作区路径：

```bash
python D:\claude-hub\tools\qq-helper.py status
```

在每个工作区启动 Claude Code：

```bash
cd "C:\Users\xxx\Desktop\claude ADa"
claude
```

Claude Code 启动后会自动（按 CLAUDE.md 规则）：
1. 运行 `python tools/build-index.py` 重建文件索引
2. 启动 Monitor 监听 QQ 消息
3. 开始处理 QQ 消息

---

## 第五步：验证 QQ 连通性

在 QQ 上给任意一个配置好的机器人发消息，确认能收到回复。

---

## 后续维护

定期运行以下命令清理旧数据：

```bash
python tools/qq-helper.py trim
```

查看系统状态：

```bash
python tools/qq-helper.py status
```

---

## 故障排查

### 机器人不回复
1. 确认 Hub 主程序（Claude Hub.exe）正在运行
2. 确认对应机器人的 Claude Code 终端正在运行
3. 运行 `python tools/qq-helper.py status` 查看待处理消息

### 添加新机器人
```bash
python D:\claude-hub\install-hub.py   # 选择「添加机器人」模式
```

### 卸载
运行 `D:\claude-hub\uninstall.bat`
