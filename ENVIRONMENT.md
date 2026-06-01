# 环境要求

本文档说明 **AI 视觉设计助手**（`ai-design-modifier-delivery`）运行所需的环境，便于用 `pyenv` / `asdf` 等切换 Python 版本。

## 一句话结论

| 运行时 | 是否必需 | 说明 |
|--------|----------|------|
| **Python 3** | **必需** | 主服务由 `backend/app.py` 提供，通过 `./start.sh` 启动 |
| **Node.js** | **本机不必** | `./start.sh` / `./dev.sh` 不依赖 Node；**`./deploy.sh` 服务器部署**需 PM2（脚本可自动装 Node + PM2） |
| **即梦 CLI / Lovart / ComfyUI / SD** | 按需 | 生图、修图能力取决于 `.env` 配置 |

---

## Python 版本

### 推荐

- **Python 3.10 ~ 3.14**（项目当前虚拟环境为 **3.14.4**）
- 使用系统自带的 `python3` 即可；`start.sh` 会在 `backend/.venv` 创建独立虚拟环境

### 版本切换示例（pyenv）

```bash
cd /path/to/ai-design-modifier-delivery

# 安装并选用 Python（示例 3.12）
pyenv install 3.12.8 -s
pyenv local 3.12.8

# 重建虚拟环境
rm -rf backend/.venv
./start.sh
```

### 版本切换示例（conda）

```bash
conda create -n ai-design python=3.12 -y
conda activate ai-design
rm -rf backend/.venv
./start.sh
```

> **注意**：切换 Python 大版本后，请删除 `backend/.venv` 再执行 `./start.sh`，否则会沿用旧解释器安装的包。

---

## Node.js

**运行本项目主流程不需要安装 Node.js。**

仓库中的 `backend/main.py` 是早期 FastAPI 演示版，**不是** `./start.sh` 的启动入口。若你只用 `./start.sh`，可忽略 Node 版本。

若将来单独跑 `main.py`，需要 Python 环境并自行安装 `fastapi`、`uvicorn`（未列入当前 `requirements.txt`）。

---

## 系统与平台

| 项目 | 说明 |
|------|------|
| **操作系统** | macOS / Linux 均可；部分能力在 macOS 上更完整 |
| **macOS 可选** | `sips`（裁剪）、`osascript`（图片合成回退）系统自带 |
| **网络** | 调用 Lovart / 大模型 API / 下载生成图需可访问外网 |
| **磁盘** | `uploads/`、`outputs/`、`projects/` 会持续增长 |

---

## Python 依赖（`backend/requirements.txt`）

| 包 | 用途 |
|----|------|
| `beautifulsoup4` | 解析网页 HTML |
| `lxml` | HTML 解析后端 |
| `playwright` | 抓取需 JS 渲染的参考页（可选功能） |
| `Pillow` | 局部修图：裁剪、缩放、选区合成 |

安装由 `start.sh` 自动执行：

```bash
backend/.venv/bin/python3 -m pip install -r backend/requirements.txt
```

### Playwright 浏览器（首次建议执行）

网页抓取功能需要 Chromium：

```bash
cd backend
.venv/bin/python3 -m playwright install chromium
```

---

## 启动方式

### 本机开发 / 前台运行

```bash
# 项目根目录
cp .env.example .env   # 首次：按需填写 Key
./start.sh             # 生产前台
# ./dev.sh             # 开发热重载
```

- 默认端口：**8000**（可在 `.env` 中设置 `PORT`）
- 若 8000 被占用，服务会自动尝试 8001、8002…，**请以终端打印的地址为准**
- 浏览器访问：`http://localhost:<端口>`

### 服务器部署（`deploy.sh`）

适用于 Linux 服务器长期运行，由 **PM2** 守护 `backend/app.py`。

```bash
chmod +x deploy.sh
./deploy.sh              # 默认：检查 Python → venv → 依赖 → PM2 启动/重载
./deploy.sh setup        # 仅装依赖并生成 ecosystem.config.cjs，不启动
./deploy.sh status       # 状态 + 健康检查
./deploy.sh logs         # PM2 日志
./deploy.sh restart      # 重启
./deploy.sh stop         # 停止
./deploy.sh update       # git pull + 依赖更新 + PM2 重载
```

**脚本会自动：**

- 检查 Python **3.10+**（CentOS 7 等旧系统可自动装 Miniconda Python 3.10）
- 创建 `backend/.venv` 并安装依赖
- 安装 **Node.js + PM2**（可用 `SKIP_NODE_INSTALL=1` 跳过）
- 生成 `ecosystem.config.cjs`（PM2 进程名默认 `aizhushou-age`）
- 创建 `uploads/`、`outputs/`、`logs/`

**环境变量（可选）：**

| 变量 | 说明 |
|------|------|
| `PM2_APP_NAME` | PM2 进程名，默认 `aizhushou-age` |
| `SKIP_NODE_INSTALL=1` | 不自动安装 Node/PM2 |
| `GIT_BRANCH` | `./deploy.sh update` 拉取分支，默认 `main` |

开机自启（服务器上执行一次）：

```bash
pm2 startup
pm2 save
```

### 远程一键部署（`./deploy.sh remote`）

在本机执行，通过 **sshpass + rsync + SSH** 将项目同步到测试机并远端运行 `./deploy.sh`。

**`.env` 必填：**

| 变量 | 示例 | 说明 |
|------|------|------|
| `TEST_SERVICE_URL` | `your-server.example.com` | 目标 IP 或 hostname |
| `TEST_ACCOUNT` | `deploy_user` | SSH 用户 |
| `TEST_PASSWORD` | `***` | SSH 密码 |
| `PORT` | `<your-port>` | 原样同步到远端，脚本不改写 |

**本机需安装：** `ssh`、`rsync`、`sshpass`

```bash
# macOS
brew install hudochenkov/sshpass/sshpass

# Debian/Ubuntu
apt install sshpass rsync
```

**命令：**

```bash
./deploy.sh remote         # 同步 + 远端 deploy + PM2
./deploy.sh remote sync    # 仅 rsync，不重启服务
```

**远端路径：** 由 `deploy.sh` 中的 `REMOTE_DIR` 控制（自动 `mkdir -p`）

**同步规则：**

- **包含：** 项目代码、`.env`、`projects/`、`deploy.sh` 等
- **排除：** `backend/.venv/`、`.git/`、`uploads/`、`outputs/`、`logs/`、`ecosystem.config.cjs` 等

**CentOS 7 测试机额外行为：**

1. 基础依赖使用 `backend/requirements-deploy.txt`（避免 playwright/greenlet 编译失败）
2. 部署时自动安装 **rembg** 相关包（`numpy<2` + onnxruntime + rembg）
3. **Playwright 浏览器**因 glibc 2.17 无法安装，网页参考抓取仅支持静态 HTTP；JS 渲染页不可用

**测试机访问 Lovart：**

若生图报错 `Network is unreachable`，说明测试机无法直连 `lgw.lovart.ai`。在 `.env` 配置 HTTP 代理后重新 `./deploy.sh remote`：

```bash
HTTP_PROXY=http://proxy.example.com:1080
HTTPS_PROXY=http://proxy.example.com:1080
http_proxy=http://proxy.example.com:1080
https_proxy=http://proxy.example.com:1080
NO_PROXY=127.0.0.1,localhost
no_proxy=127.0.0.1,localhost
```

应用启动时会读取 `.env` 写入 `os.environ`，`urllib` 请求 Lovart 会自动走代理。可用 curl 对比：

```bash
curl -I https://lgw.lovart.ai -x http://proxy.example.com:1080
```

连通后根路径可能返回 **404**，属正常（API 在 `/v1/openapi/...`）。

**多人同时使用与生图排队：**

测试环境团队共用单 PM2 进程时，Lovart 生图经内存队列串行（`LOVART_MAX_CONCURRENCY`，默认 1）。前端提交 `POST /api/generation/jobs` 后立即返回 `job_id`，可轮询排队位置与进度；浏览器 `localStorage` 中的 `client_id` 用于「每人同时仅 1 个主生图任务」。`pm2 reload` 或 `./deploy.sh update` 会清空内存队列，进行中的任务标记失败，需用户重新生成。可选变量：`LOVART_QUEUE_MAX`、`LOVART_JOB_TTL`、`LOVART_JOB_MAX_SECONDS`、`LOVART_ETA_AVG_SECONDS`（见 `.env.example`）。

设计规格详见 [docs/superpowers/specs/2026-05-25-remote-deploy-design.md](./docs/superpowers/specs/2026-05-25-remote-deploy-design.md)。

---

## 外部服务（按 `.env` 选配）

### 生图后端 `IMAGE_BACKEND`

| 值 | 依赖 |
|----|------|
| `lovart`（推荐） | 每组 `LOVART_ACCESS_KEY_HLL`/`_XDT` 与对应 `LOVART_SECRET_KEY_*` |
| `dreamina` | 已安装 **即梦 CLI**（`dreamina` 命令，或配置 `DREAMINA_BIN`） |
| `comfyui` | 本地 ComfyUI（`COMFYUI_API_URL`、`COMFYUI_CHECKPOINT`） |
| `stable_diffusion` | 本地 SD WebUI（`SD_API_URL`） |
| `auto` | 有 Lovart Key 时优先 Lovart，否则即梦 |

### 项目组门禁

| 变量 | 说明 |
|------|------|
| `PROJECT_GATE_ENABLED` | `1`（默认）开启门禁；`0` / `false` 关闭（恢复项目组下拉，API 不校验 Bearer） |

门禁**开启**时，打开或刷新页面需先选择项目组并输入密码（内存 Token，刷新后需重登）：

| 项目组 | 环境变量 | 示例 |
|--------|----------|------|
| 画啦啦 | `PROJECT_PASSWORD_HLL` | `hll2026` |
| 小灯塔 | `PROJECT_PASSWORD_XDT` | `xdt2026` |

解锁后 API 请求须带：`Authorization: Bearer <token>`。

### 项目组大模型 / Lovart Key（必须分组，禁止回落无后缀全局 Key）

| 项目组 | Lovart（迁移） | DeepSeek 润色（必填） |
|--------|----------------|------------------------|
| 画啦啦 | `LOVART_ACCESS_KEY_HLL`（原主 Key） | `DEEPSEEK_API_KEY_HLL` |
| 小灯塔 | `LOVART_ACCESS_KEY_XDT`（原 `_2` 备用 Key） | `DEEPSEEK_API_KEY_XDT` |

可选：`QIANWEN_API_KEY_*`、`KIMI_API_KEY_*`、`DOUBAO_API_KEY_*`；同组 Lovart 备用 `LOVART_ACCESS_KEY_HLL_2` 等。

未配置对应组的 `DEEPSEEK_API_KEY_*` 时，「AI 分析关键词」会走本地规则拼接。

**公司 TokenHub / AgentHub（推荐）**：入口 [https://agenthub.vipthink.cn/](https://agenthub.vipthink.cn/)，扫码登录 → 创建 Key → **画啦啦、小灯塔各写一份**（目前仅一个 Key 时可先填相同值到 `_HLL` 与 `_XDT`）：

```bash
DEEPSEEK_API_KEY_HLL=sk-user-你的Key
DEEPSEEK_API_KEY_XDT=sk-user-你的Key
DEEPSEEK_BASE_URL=https://agenthub.vipthink.cn
# 模型 ID 须与 AgentHub 列表一致，例如 claude-haiku-4-5-20251001（不是简称 claude-haiku-4-5）
DEEPSEEK_MODEL=claude-haiku-4-5-20251001
```

说明：

- 环境变量名 `DEEPSEEK_*` 是历史命名，实际可走 Claude / GPT 等任意 AgentHub 已开通模型。
- **不要用公网 `https://dtok.ai`**：公司发的 `sk-user-...` 只在 AgentHub 网关有效，填 `dtok.ai` 会 `401 Invalid token` 并降级为本地规则拼接（每次结果一样）。
- **居家 / 外网**需先连公司 VPN，再访问 AgentHub 与本服务。
- `DEEPSEEK_BASE_URL` 不要带 `/v1` 后缀（程序会自动拼接 `/v1/chat/completions`）。

---

## 目录结构（运行时自动创建）

```
ai-design-modifier-delivery/
├── .env                 # 环境变量（勿提交密钥）
├── deploy.sh            # 服务器 / 远程 PM2 部署
├── start.sh             # 本机前台启动
├── dev.sh               # 开发热重载
├── backend/
│   ├── app.py           # 主服务（当前使用）
│   ├── main.py          # 旧版 FastAPI 演示（非 start.sh 入口）
│   ├── .venv/           # Python 虚拟环境（git 可忽略）
│   └── requirements.txt
├── uploads/             # 上传图
├── outputs/             # 生成结果
├── projects/            # 项目组参考图
└── history.json         # 历史记录
```

---

## 常见问题

### `No module named 'PIL'`

未安装 Pillow，执行：

```bash
backend/.venv/bin/python3 -m pip install Pillow
# 或重新 ./start.sh（会读 requirements.txt）
```

### 修图 / 生图接口 404

1. 确认用 `./start.sh` 启动的是 `app.py`，不是旧进程或其它端口上的服务  
2. 重启：`Ctrl+C` 后再次 `./start.sh`  
3. 浏览器地址与终端显示的端口一致  

### 切换 Python 后各种 import 报错

```bash
rm -rf backend/.venv
./start.sh
```

### 远程部署 `sshpass: command not found`

本机安装 sshpass 后重试 `./deploy.sh remote`（见上文「远程一键部署」）。

### 测试机 Lovart `Network is unreachable`

在 `.env` 配置 `HTTP_PROXY` / `HTTPS_PROXY`，执行 `./deploy.sh remote` 同步并重载 PM2。

### CentOS 7 上 rembg / Playwright

- **rembg**：`deploy.sh` 会自动安装；若重建 venv 后丢失，再执行一次 `./deploy.sh remote`
- **Playwright**：CentOS 7 不支持 Chromium 运行时，仅静态网页抓取可用

### `pip` 安装 `lxml` 时报 `TomlError` / `pyproject.toml`

多见于 **Python 3.8 + 旧 pip（如 19.x）**。`start.sh` 会先升级 pip；若仍失败，请改用 **Python 3.10+** 并重建虚拟环境：

```bash
python3 --version   # 需 >= 3.10
rm -rf backend/.venv
./start.sh
```

---

## 快速检查清单

```bash
python3 --version          # 建议 >= 3.10
./start.sh                 # 首次会自动建 venv、装依赖
# 可选
backend/.venv/bin/python3 -m playwright install chromium
backend/.venv/bin/python3 -c "from PIL import Image; print('OK')"
```

---

*文档根据仓库 `deploy.sh`、`start.sh`、`backend/app.py`、`backend/requirements.txt`、`.env.example` 整理。*
