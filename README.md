# ai-design-modifier-delivery

> **小A做的工具** — 团队内部的 AI 视觉设计助手，由小A搭建与维护。

AI 视觉设计助手：根据需求描述生成/变体图片，支持局部修图、项目组参考图、网页参考抓取与历史记录。主服务为 Python 单进程 Web 应用，前端页面内嵌在 `backend/app.py` 中。

## 功能概览

- **需求解析与多图变体**：输入设计需求，可一次生成多张候选图
- **局部修图**：上传图片 + 选区，裁剪、缩放与合成（依赖 Pillow）
- **项目组参考**：从 `projects/` 目录选择参考素材
- **网页参考抓取**（可选）：需安装 Playwright Chromium
- **生图后端**：Lovart 龙虾（默认）；可选即梦 / ComfyUI / Stable Diffusion（`.env` 中 `IMAGE_BACKEND`）
- **辅助工具**：多尺寸导出、框选裁切、呼吸 GIF、GIF 转 SVGA
- **AI 关键词分析**：配置 DeepSeek / 通义 / Kimi / 豆包等任一 API Key 后启用

## 环境要求

| 项目 | 要求 |
|------|------|
| Python | **3.10+**（推荐 3.12；不支持 3.8/3.9） |
| Node.js | **不需要** |
| 操作系统 | macOS / Linux（macOS 上部分图片处理能力更完整） |

更详细的版本切换、依赖说明与 FAQ 见 [ENVIRONMENT.md](./ENVIRONMENT.md)。

## 快速开始

```bash
# 1. 克隆或进入项目根目录
cd ai-design-modifier-delivery

# 2. 确认 Python 版本
python3 --version   # 需 >= 3.10

# 3. 配置环境变量（首次）
cp .env.example .env
# 编辑 .env：至少填写 Lovart Key；可选 LLM Key 用于 AI 关键词分析

# 4. 启动（自动创建 venv、升级 pip、安装依赖）
chmod +x start.sh dev.sh
./start.sh          # 生产模式
# ./dev.sh          # 开发模式：改 backend/*.py 自动重启，改 templates/index.html 刷新浏览器即可
```

启动成功后，在浏览器打开终端打印的地址（默认 `http://localhost:8000`；端口被占用时会自动尝试 8001、8002…）。

### 可选：Playwright 浏览器

若需「从网页抓取参考」功能，首次请执行：

```bash
cd backend
.venv/bin/python3 -m playwright install chromium
```

## 配置说明

复制 `.env.example` 为 `.env` 后按需修改，常用项如下：

| 变量 | 说明 |
|------|------|
| `PORT` | 服务端口，默认 `8000` |
| `LOVART_ACCESS_KEY` / `LOVART_SECRET_KEY` | Lovart 主 Key；可用 `LOVART_ACCESS_KEY_2` 等配置备用，并发/额度受限时自动切换 |
| `DEEPSEEK_API_KEY` 等 | 至少配置一个，用于 AI 关键词分析 |

完整项与注释见 [.env.example](./.env.example)。**请勿将 `.env` 提交到 Git。**

## 目录结构

```
ai-design-modifier-delivery/
├── start.sh              # 推荐启动入口
├── .env / .env.example   # 环境变量
├── dev.sh                # 开发模式（热重载）
├── backend/
│   ├── app.py            # 主服务（start.sh 启动此文件）
│   ├── templates/index.html  # Web UI
│   ├── gif_maker.py / image_crop.py / multi_size_export.py / gif_to_svga/
│   ├── requirements.txt
│   └── .venv/            # 本地虚拟环境（可删除重建）
├── uploads/              # 上传图片
├── outputs/              # 生成结果
├── projects/             # 项目组参考图
└── history.json          # 历史记录
```

> `backend/main.py` 为早期 FastAPI 演示，**不是**当前启动入口。

## 常见问题

### `pip` 安装 `lxml` 报错（TomlError / pyproject.toml）

多见于 **Python 3.8 + 旧 pip**。请升级到 Python 3.10+ 并重建虚拟环境：

```bash
python3 --version          # 确认 >= 3.10
rm -rf backend/.venv
./start.sh
```

macOS 可安装新版本：`brew install python@3.12`，再用该解释器建 venv 后执行 `./start.sh`。

### 切换 Python 后 import 失败

```bash
rm -rf backend/.venv
./start.sh
```

### `No module named 'PIL'`

重新执行 `./start.sh`，或手动：`backend/.venv/bin/python3 -m pip install Pillow`。

### 接口 404 或页面打不开

确认使用 `./start.sh` 启动，且浏览器地址与**终端打印的端口**一致；修改代码或 `.env` 后需 `Ctrl+C` 重启。

## 开发说明

- 主逻辑：`backend/app.py`；UI：`backend/templates/index.html`
- 生图客户端：`backend/lovart_client.py`（`comfyui_client.py` / `sd_client.py` 保留未接入）
- 切换 Python 大版本后务必删除 `backend/.venv` 再运行 `./start.sh`

## 相关文档

- [ENVIRONMENT.md](./ENVIRONMENT.md) — 环境、依赖、外部服务与完整 FAQ

---

**说明**：本项目为 **小A** 开发的交付工具；使用或部署问题可先查阅本文档与 `ENVIRONMENT.md`，功能与配置变更以小A维护的版本为准。
