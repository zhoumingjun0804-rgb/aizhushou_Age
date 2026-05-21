# 环境要求

本文档说明 **AI 视觉设计助手**（`ai-design-modifier-delivery`）运行所需的环境，便于用 `pyenv` / `asdf` 等切换 Python 版本。

## 一句话结论

| 运行时 | 是否必需 | 说明 |
|--------|----------|------|
| **Python 3** | **必需** | 主服务由 `backend/app.py` 提供，通过 `./start.sh` 启动 |
| **Node.js** | **不必** | 当前入口不依赖 Node；前端页面内嵌在 Python 中 |
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

```bash
# 项目根目录
cp .env.example .env   # 首次：按需填写 Key
./start.sh
```

- 默认端口：**8000**（可在 `.env` 中设置 `PORT`）
- 若 8000 被占用，服务会自动尝试 8001、8002…，**请以终端打印的地址为准**
- 浏览器访问：`http://localhost:<端口>`

---

## 外部服务（按 `.env` 选配）

### 生图后端 `IMAGE_BACKEND`

| 值 | 依赖 |
|----|------|
| `lovart`（推荐） | `LOVART_ACCESS_KEY`、`LOVART_SECRET_KEY` |
| `dreamina` | 已安装 **即梦 CLI**（`dreamina` 命令，或配置 `DREAMINA_BIN`） |
| `comfyui` | 本地 ComfyUI（`COMFYUI_API_URL`、`COMFYUI_CHECKPOINT`） |
| `stable_diffusion` | 本地 SD WebUI（`SD_API_URL`） |
| `auto` | 有 Lovart Key 时优先 Lovart，否则即梦 |

### 关键词分析（至少配置一个 LLM Key）

- `DEEPSEEK_API_KEY`
- `QIANWEN_API_KEY`
- `KIMI_API_KEY`
- `DOUBAO_API_KEY`

未配置时仍可使用，但「AI 分析关键词」会走本地规则拼接。

---

## 目录结构（运行时自动创建）

```
ai-design-modifier-delivery/
├── .env                 # 环境变量（勿提交密钥）
├── start.sh             # 启动脚本
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

*文档根据仓库 `start.sh`、`backend/app.py`、`backend/requirements.txt`、`.env.example` 整理。*
