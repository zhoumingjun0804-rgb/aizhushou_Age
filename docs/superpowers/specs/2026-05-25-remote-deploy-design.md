# 远程部署（deploy.sh remote）设计规格

**日期：** 2026-05-25（2026-05-26 更新：代理、CentOS 7 extras）  
**状态：** 已实现  
**范围：** 在本机执行 `./deploy.sh remote`，通过 SSH 将项目同步到测试服务器并在远端应用目录以 PM2 运行。

---

## 背景与目标

团队已在 `.env` 中配置测试环境连接信息：

| 变量 | 示例值 | 用途 |
|------|--------|------|
| `TEST_SERVICE_URL` | `your-server.example.com` | 目标服务器 IP / hostname |
| `TEST_ACCOUNT` | `deploy_user` | SSH 用户名 |
| `TEST_PASSWORD` | `***` | SSH 密码 |
| `PORT` | `<your-port>`（用户手动设置） | 服务端口，原样同步到远端 |

目标：一条命令完成「上传代码 → 远端安装依赖 → PM2 启动」，远端目录由脚本配置控制（不存在则创建）。

---

## 非目标

- 不在本机改 `PORT`（由用户自行在 `.env` 设置）
- 不支持多环境 profile（仅 TEST_* 一套）
- 不实现远端 `remote logs` 子命令（后续可加）
- 不使用 git pull 作为部署方式

---

## 方案选择

**选用：rsync + sshpass + SSH 远程执行 `deploy.sh`**

理由：增量同步、exclude 清晰、复用现有 `deploy.sh` 的 venv/PM2 逻辑。

---

## 命令接口

```bash
./deploy.sh remote        # 同步 + 远端 ./deploy.sh（默认）
./deploy.sh remote sync   # 仅 rsync，不在远端启动
```

`remote sync` 用于只更新文件、不触发 PM2 重载的场景。

---

## 配置读取

从项目根 `.env` 解析（与现有 `load_port` 风格一致）：

- `TEST_SERVICE_URL` — 必填，仅 IP 或 hostname，不含 `http://` 与路径
- `TEST_ACCOUNT` — 必填
- `TEST_PASSWORD` — 必填
- `PORT` — 随 `.env` 原样上传，不在脚本内改写

远端固定路径：`REMOTE_DIR=<remote-app-dir>`

`.env.example` 增加上述 TEST_* 变量注释说明（不含真实密码）。

---

## 同步规则

### 上传

项目根目录下除排除项外的全部文件，**包含 `.env`**（含 Lovart Key 与 TEST_*）。

### 排除（rsync `--exclude`）

| 路径 | 原因 |
|------|------|
| `backend/.venv/` | 远端自建虚拟环境 |
| `.git/` | 无需版本库 |
| `uploads/`、`outputs/` | 运行时数据 |
| `logs/` | PM2 日志 |
| `__pycache__/`、`*.pyc` | 缓存 |
| `.dev-server.pid`、`.dev-server.log` | 本地开发残留 |
| `ecosystem.config.cjs` | 远端 deploy 时生成 |

---

## 执行流程

```
本机 ./deploy.sh remote
  │
  ├─ 1. 检查本机依赖：sshpass、rsync、ssh
  ├─ 2. 从 .env 加载 TEST_*，校验非空
  ├─ 3. SSH：mkdir -p <REMOTE_DIR>
  ├─ 4. rsync 同步到 TEST_ACCOUNT@TEST_SERVICE_URL:<REMOTE_DIR>/
  ├─ 5. SSH：chmod +x <REMOTE_DIR>/deploy.sh
  └─ 6. SSH：cd <REMOTE_DIR> && ./deploy.sh
         └─ 远端：venv、pip、PM2、健康检查（现有逻辑）
```

`remote sync` 在步骤 4 后结束，不执行 5–6 中的 deploy（chmod 仍执行以保证后续可手动 deploy）。

---

## SSH 连接

- 认证：**密码**，通过 `sshpass` 读取 `TEST_PASSWORD`（环境变量 `SSHPASS`，避免命令行明文）
- 端口：默认 **22**（未配置 `TEST_SSH_PORT` 时不扩展）
- Host key：`-o StrictHostKeyChecking=accept-new`，避免首次连接交互阻塞
- 连接串：`${TEST_ACCOUNT}@${TEST_SERVICE_URL}`

本机缺少 `sshpass` 时提示安装，例如：

- macOS: `brew install hudochenkov/sshpass/sshpass` 或 `brew install esolitos/ipa/sshpass`
- Debian/Ubuntu: `apt install sshpass`

---

## 错误处理

| 场景 | 行为 |
|------|------|
| TEST_* 未配置 | 报错退出，提示填写 `.env` |
| sshpass/rsync 缺失 | 报错并给出安装命令 |
| SSH 连接失败 | 打印错误，不继续 rsync |
| rsync 失败 | 退出，不执行远端 deploy |
| 远端 deploy.sh 失败 | 非零退出码透传，提示用户 SSH 登录查看 `logs/pm2-*.log` |

---

## 安全说明

- `.env` 含密钥与 SSH 密码，**不得提交 Git**（已在 `.gitignore`）
- 密码仅通过 `SSHPASS` 传给 `sshpass`，不在日志中 echo
- 测试环境为内网 IP，接受 `accept-new`；生产环境应另行设计

---

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `deploy.sh` | 新增 `remote` / `remote sync` 子命令及相关函数 |
| `.env.example` | 增加 `TEST_SERVICE_URL`、`TEST_ACCOUNT`、`TEST_PASSWORD` 注释 |

---

## 验收标准

1. 本机执行 `./deploy.sh remote`，远端应用目录被创建且含最新代码
2. 远端 PM2 进程 `aizhushou-age` 运行，监听 `.env` 中 `PORT`
3. 浏览器访问 `http://<server-host>:<port>/` 可打开页面
4. 二次执行 `./deploy.sh remote` 可增量更新并成功 reload
5. `./deploy.sh remote sync` 只同步文件，不改变 PM2 运行状态（除非用户随后手动 deploy）

---

## 自审记录

- [x] 无 TBD / TODO 占位
- [x] 与 brainstorm 结论一致（A/A/A/B 选项）
- [x] 范围单一，可在一个 implementation plan 内完成
- [x] PORT 行为明确：由用户在 `.env` 中设置，脚本不改写

---

## 实现补充（2026-05-26）

| 项 | 说明 |
|----|------|
| CentOS 7 Python | Miniconda 3.10 安装于 `/opt/aizhushou-python` |
| 依赖 | 基础 `requirements-deploy.txt` + 自动 rembg extras |
| Playwright | CentOS 7 跳过浏览器安装 |
| Lovart 出网 | `.env` 中 `HTTP_PROXY` / `HTTPS_PROXY`，随 remote 同步 |
| 用户文档 | `README.md`、`ENVIRONMENT.md` 部署章节 |
