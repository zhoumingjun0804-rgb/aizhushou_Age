---
name: init
description: >-
  Onboards the agent to ai-design-modifier-delivery: reads ENVIRONMENT.md,
  project layout, startup, and .env configuration. Use when the user runs /init,
  says "init", or asks to familiarize with this project before coding.
disable-model-invocation: true
---

# /init — 项目上手

用户显式调用 `/init` 时执行本技能。目标：在动手改代码前建立对本仓库的准确心智模型，并用中文向用户汇报要点。

## 必读文件（按顺序）

1. [ENVIRONMENT.md](../../../ENVIRONMENT.md) — Python 版本、依赖、启动、外部服务、FAQ
2. [.env.example](../../../.env.example) — 可配置项与含义（**不要**读取或回显 `.env` 中的密钥）
3. [start.sh](../../../start.sh) — 唯一推荐启动入口
4. `backend/app.py` 文件头（约前 100 行）— 确认 `BASE_DIR`、目录常量、`_load_env_file`
5. 按需扫一眼：`backend/lovart_client.py`、`backend/comfyui_client.py`、`backend/sd_client.py`、`backend/requirements.txt`

## 禁止事项

- 不要读取、复制、提交或向用户展示 `.env` 里的 API Key / Secret
- 不要把 `backend/main.py` 当成主入口（`start.sh` 启动的是 `app.py`）
- 不要假设需要 Node.js（主流程不依赖）

## 上手检查清单

复制并逐项完成：

```
/init 进度:
- [ ] 已读 ENVIRONMENT.md
- [ ] 已读 .env.example（未泄露 .env 密钥）
- [ ] 已确认启动：./start.sh → backend/app.py
- [ ] 已理清运行时目录：uploads/ outputs/ projects/ history.json
- [ ] 已弄清生图后端 IMAGE_BACKEND 与对应客户端模块
- [ ] 已弄清 LLM Key（DEEPSEEK / QIANWEN / KIMI / DOUBAO）用于关键词分析
```

## 向用户汇报的模板

用中文、简洁、完整句子，按下面结构输出（无数据则写「未配置」而非猜测）：

```markdown
## 项目概览
- 名称与用途：（来自 ENVIRONMENT.md / app.py 注释）
- 技术栈：Python 3.10+，无 Node 主依赖

## 如何运行
- 启动：`./start.sh`
- 端口：默认 8000（.env 的 PORT；占用时会递增）
- 访问：终端打印的 http://localhost:<端口>

## 目录与数据
- 上传 / 输出 / 项目组 / 历史 各路径
- 主代码：backend/app.py + *_client.py

## 配置要点（仅列项名，不列密钥值）
- IMAGE_BACKEND 当前逻辑
- Lovart / 即梦 / ComfyUI / SD 各自需要哪些变量
- 至少一个 LLM Key 是否已在 .env 中「存在」（只说有/无，不说值）

## 开发时注意
- 改 Python 大版本后：`rm -rf backend/.venv && ./start.sh`
- Playwright 抓取：需 `playwright install chromium`
- 优先复用项目已有客户端与封装，避免重复实现 HTTP/轮询逻辑

## 我已准备好
说明接下来用户若提出需求，你会从哪些模块/文件入手（1–3 条）。
```

## 何时结束

- 检查清单全部勾选
- 汇报已按模板给出
- 若用户紧接着有具体任务，在汇报末尾用一句话衔接其目标与建议入口文件
