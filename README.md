# Telegram ↔ Claude Code Bridge

把 [Claude Code](https://claude.com/claude-code) CLI 接到 Telegram —— 在 Telegram 里直接跟 Claude Code 聊天，让它在你的服务器上读文件、跑命令、写代码。

## 它是什么

一个小的 Python 程序，用 systemd 常驻在你自己的 Linux 服务器上：

- **接收** Telegram 消息
- **转发**给一个长驻的 `claude -p --input-format=stream-json` 子进程
- **把 Claude 的回复发回**Telegram

每个 Telegram 聊天（chat_id）对应一个独立的 `claude` 子进程，**上下文互相隔离**。支持多用户白名单、空闲自动回收、并发上限控制。

```
Telegram ──▶ python bridge.py ──▶ claude -p（每个 chat 一个子进程）
                                    │
                                    ├─ chat A 的 claude（session A 的上下文）
                                    ├─ chat B 的 claude（session B 的上下文）
                                    └─ ...
```

## 适用场景

✅ 远程用手机指挥服务器上的 Claude Code 写代码、查日志、改配置  
✅ 多人共享一台服务器上的 Claude Code，每人聊天上下文独立  
✅ 想把 Claude 当 Telegram 聊天机器人用

❌ 生产环境暴露给陌生人（见 [安全警告](#-安全警告)）

## 前置要求

- Linux 服务器（示例用 Ubuntu，其他发行版同理）
- Python 3.10+
- [Claude Code](https://docs.claude.com/en/docs/claude-code) 已经装好并登录（`claude` 命令能用）
- 一个 Telegram 账号，用来找 @BotFather 新建 bot

> **关于 Claude Code 的认证方式**：如果你用官方 claude.ai 登录，无需额外配置。如果想让 Claude Code 背后走自定义模型（比如 litellm 代理到 Azure OpenAI），在 `~/.claude/settings.json` 里配 `env` 字段即可，这个 bridge 不关心 Claude Code 怎么接模型。

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/YOUR_USER/telegram-claude-bridge.git ~/claude-tg-bot
cd ~/claude-tg-bot
```

> 建议就放在 `~/claude-tg-bot`，因为 `claude-tg-bot.service` 里用 `%h/claude-tg-bot` 作为默认路径。放别的位置记得改 service 文件。

### 2. 创建 Python 虚拟环境 + 装依赖

```bash
# Ubuntu 上可能要先装 venv
sudo apt-get install -y python3-venv

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. 创建 Telegram Bot

1. 在 Telegram 里搜 **@BotFather**，发 `/newbot`
2. 按提示给 bot 起名字和用户名（用户名必须 `_bot` 结尾）
3. BotFather 会给你一个 token，形如 `7234567890:AAEH1234abcd...`

### 4. 配置 .env

```bash
cp .env.example .env
chmod 600 .env         # 保护 token
```

用你喜欢的编辑器填 `.env`：

- `TELEGRAM_BOT_TOKEN` — 上一步拿到的 token
- `ALLOWED_USER_IDS` — **先随便填一个占位数字**（比如 `0`），下文会告诉你怎么拿到自己的真实 id
- `CLAUDE_BIN` — 用 `which claude` 查出来的绝对路径
- 其他选项见 `.env.example` 里的注释

### 5. 启动服务

**用 systemd（推荐，会开机自启）：**

```bash
# 让 user-level systemd 在你注销后也能跑服务
loginctl enable-linger $USER

cp claude-tg-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-tg-bot.service

# 查看状态
systemctl --user status claude-tg-bot.service
journalctl --user -u claude-tg-bot -f
```

**或临时手动跑（调试时方便）：**

```bash
.venv/bin/python bridge.py
```

### 6. 拿到你自己的 Telegram user id

1. 在 Telegram 里打开你的 bot（BotFather 会给你一个 `t.me/xxx_bot` 链接）
2. 发 `/start`
3. Bot 会回复 `unauthorized (your id: 123456789)` —— 记下那个数字

### 7. 把你的 id 加进白名单

```bash
# 编辑 .env，把 ALLOWED_USER_IDS 改成你真实的 id（多个 id 用逗号分隔）
nano .env
# 或一行替换：
sed -i 's/^ALLOWED_USER_IDS=.*/ALLOWED_USER_IDS=123456789/' .env

# 重启服务让变更生效
systemctl --user restart claude-tg-bot.service
```

再去 Telegram 发 `/start`，这次应该回复 `connected. chat_id=..., session=...`，说明联通了。开始聊天吧。

## 使用

### 基本命令

| 命令 | 作用 |
|---|---|
| 任意文字消息 | 转发给 claude，等 claude 回复 |
| `/start` | 显示当前 chat 的 session id，以及是否已授权 |
| `/newsession` | 杀掉当前 chat 的 claude 子进程，下条消息会开一个新 session（丢掉旧上下文） |

### 新增白名单用户

让对方去找 bot 发 `/start`，从 bot 返回的 `unauthorized (your id: XXX)` 拿到对方 id，然后：

```bash
sed -i 's/^ALLOWED_USER_IDS=.*/ALLOWED_USER_IDS=原有id,新id/' ~/claude-tg-bot/.env
systemctl --user restart claude-tg-bot.service
```

⚠️ 重启会杀掉所有 chat 的 claude 子进程，**所有会话上下文都会丢**。

### 查看日志

```bash
# 实时跟踪
journalctl --user -u claude-tg-bot -f

# 最近 100 行
journalctl --user -u claude-tg-bot -n 100 --no-pager
```

## 配置项说明

所有可调项都在 `.env`，改完后 `systemctl --user restart claude-tg-bot.service` 生效。

| 键 | 默认值 | 说明 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | （必填） | BotFather 给的 token |
| `ALLOWED_USER_IDS` | （必填） | 允许用 bot 的 Telegram user id，逗号分隔 |
| `CLAUDE_CWD` | `~/` | claude 子进程的工作目录 |
| `CLAUDE_BIN` | `/home/USER/.local/bin/claude` | claude 可执行文件路径 |
| `IDLE_TIMEOUT_SECONDS` | `1800`（30 分钟） | chat 空闲多久后回收其 claude 子进程 |
| `MAX_SESSIONS` | `5` | 同时活跃的 claude 子进程上限；超过会踢最久没用的 |

## 架构简要

- `bridge.py` 是主程序，启动一个 Telegram long-polling 客户端
- `SessionManager` 维护 `chat_id → ClaudeSession` 映射
- `ClaudeSession` 封装一个 `claude -p --input-format=stream-json --output-format=stream-json` 子进程
- 收到 Telegram 消息：
  1. 查/创建当前 chat 的 `ClaudeSession`
  2. 拿 session 锁（同 chat 串行）
  3. 把 `{"type":"user","message":{"role":"user","content":"..."}}` 写进 claude stdin（NDJSON）
  4. 读 claude stdout 的事件流，收到 `type=result` 就把 `.result`（或累积的 `assistant` text block）发回 Telegram
- 后台协程每 60 秒扫一次 idle session，超时就 `stop()` 并从 manager 删掉

## ⚠️ 安全警告

**请在读完这一整节后再启动服务。**

Bridge 默认给 claude 加了 `--dangerously-skip-permissions`，意思是：

> Claude 在这台机器上可以**读任何文件、写任何文件、执行任何命令**，没有任何确认步骤。

也就是说：

- **能发送 Telegram 消息给 bot 的人 = 这台机器上 shell 权限**。发 "删掉 ~/important.txt" 它就真会删。
- 白名单只保护到"谁能发消息给 bot"这一层。如果你的 Telegram 账号被盗（设备被借用 / 家人拿走），远程机器等于被控。
- 共享设备上登了你 Telegram 的话 —— 家人、同事都能操作你服务器。

### 强烈建议

1. **白名单**只加**你自己完全信任**的人
2. `.env` 文件必须 `chmod 600`
3. 敏感文件（密钥、数据库等）**不要放在** `CLAUDE_CWD` 下或下游路径
4. 考虑给 bot 创建专用的低权限 Linux 账号运行，不要直接用有 sudo 的账号
5. 不要把 bot 的 `t.me/xxx_bot` 链接或 token 发到公共频道
6. 随时能 `systemctl --user stop claude-tg-bot.service` 把服务关掉

如果你不接受这些风险，**先把 `bridge.py` 里的 `--dangerously-skip-permissions` 去掉**，换成 `--tools ""`（完全禁工具，只做文字聊天），或者 `--allowed-tools "Read,Grep,Glob"`（只允许读取，不允许写/执行）。

## 故障排查

### Bot 启动后没反应？

```bash
journalctl --user -u claude-tg-bot -n 50 --no-pager
```

常见原因：

- **`FileNotFoundError: 'claude'`** —— `CLAUDE_BIN` 路径不对，或 systemd 里 PATH 没包含 claude 所在目录
- **Telegram token 错** —— 日志会看到 401 Unauthorized
- **ALLOWED_USER_IDS 没配** —— 你发消息 bot 也不会回

### 回复显示 `(no text in claude reply)`

Claude 这轮回合只做了工具调用、没产出用户可见的文字。新版本已经从 `assistant` 事件的 text block 里累积兜底文本。如果还经常遇到，检查日志看 claude 这轮的 stderr 有没有异常。

### 第一次消息特别慢（30~60 秒）

正常。那个 chat 的 claude 子进程是**惰性启动**的，首条消息会触发冷启动（加载插件、解析模型等）。之后在 idle timeout 以内的消息会快。

### 机器内存紧张

调低 `.env` 里的 `MAX_SESSIONS`（比如 `2`）和 `IDLE_TIMEOUT_SECONDS`（比如 `300`），让 bridge 更积极地回收空闲 session。

### 想要完全重开所有 session

```bash
systemctl --user restart claude-tg-bot.service
```

## 授权协议

MIT — 见 [LICENSE](LICENSE)
