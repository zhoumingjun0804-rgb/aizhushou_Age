# 项目组门禁（密码 + 后端 Token）设计

## 1. 背景与目标

当前应用通过顶部「项目组」下拉在 **画啦啦** 与 **小灯塔** 之间切换，无访问控制；所有项目 API（参考图、设计类型、生图等）对持有 URL 的任何人开放。

目标：

- **分开管理**：两个品牌/项目组独立门禁，各自密码。
- **打开或刷新页面**必须先经过弹框：选择项目组 → 输入密码 → 进入。
- **前后端均校验**（非仅前端遮挡）：未解锁不能调用项目相关 API。
- 解锁后 **锁定当前项目组**；换项目须刷新页面重新走门禁。
- URL `?type=hll|xdt` 或 `?project=` **仅预选**项目组，不免密。
- **大模型 / 生图 Key 按项目组隔离**：画啦啦与小灯塔使用各自的 API Key（润色、Lovart 生图等），不得交叉调用。

## 2. 已确认的产品决策

| 决策 | 选择 |
|------|------|
| 安全级别 | B：前端门禁 + 后端 Token 校验 |
| 换项目 | A：锁定当前项目，刷新后重登 |
| URL 参数 | A：预选项目组，仍须密码 |
| Token 方案 | 方案 1：内存 Token + `Authorization: Bearer`（刷新页面 token 丢失，满足「刷新必登」） |
| 大模型 Key | **必须**按项目组分套配置；禁止读取无后缀全局 Key；禁止跨项目混用 |

## 3. 非目标

- 不做用户账号体系、角色权限、OAuth。
- 不做多实例间 token 共享（当前单进程部署；PM2 单实例可接受）。
- 不保护 `GET /outputs/{file}`（v1）；后续可按需加固。
- 不在 v1 做「记住密码 / 7 天免登」。
- 不在 `project.json` 明文存 Key（仍放 `.env`，与现有部署方式一致）。

## 4. 用户体验

### 4.1 门禁弹框

- 全屏遮罩 `#projectGateOverlay`，风格与现有暗色 UI 一致。
- 控件：项目组下拉（画啦啦 / 小灯塔）、密码框、`进入` 按钮、错误区 `#gateError`。
- 主界面 `#appMain` 解锁前不可交互（`visibility: hidden` 或等效）。
- `defaultProjectFromUrl()` 用于弹框预选；不预填密码。

### 4.2 解锁后

- 关闭遮罩，执行 `initUnlockedApp(data)`（原 `window.onload` 中除门禁外的逻辑）。
- 顶部「项目组」：**只读**，仅显示当前项（`disabled` 下拉或静态文案）。
- 换项目：用户 **刷新页面** → 再次弹框。

### 4.3 临时密码（`.env`，可改）

| 项目组 | 环境变量 | 初始值（示例） |
|--------|----------|----------------|
| 画啦啦 | `PROJECT_PASSWORD_HLL` | `hll2026` |
| 小灯塔 | `PROJECT_PASSWORD_XDT` | `xdt2026` |

内部名映射：`画啦啦` → `HLL`，`小灯塔` → `XDT`（与现有 `product_type` 一致）。

## 5. 后端设计

### 5.1 新模块 `backend/project_auth.py`

职责：

- 从环境变量读取各项目组密码；缺省则该项目组不可解锁（返回明确错误）。
- `unlock(project: str, password: str) -> str | None`：成功返回随机 token（`uuid4().hex`），失败返回 `None`。
- `resolve_token(token: str) -> dict | None`：返回 `{"project": "画啦啦", ...}` 或 `None`。
- 进程内 `_tokens: dict[str, {project, created_at}]`，TTL **12 小时**（防长期滥用；客户端刷新仍会丢 token）。
- 密码比对：`secrets.compare_digest`。

项目组合法值白名单：`画啦啦`、`小灯塔`（与 `projects/` 目录一致；新增项目时扩展映射表）。

### 5.2 新 API

```
POST /api/project-unlock
Content-Type: application/json
Body: { "project": "画啦啦", "password": "..." }

200: {
  "token": "<uuid>",
  "project": "画啦啦",
  "display_name": "画啦啦",
  "catalog": "folder_types",
  "product_type": "hll"
}
401: { "error": "密码错误" }
400: { "error": "未知项目组" } | { "error": "该项目组未配置密码" }
```

元数据从现有 `read_project_meta` / `detect_project_catalog` / `project_product_type` 读取，避免重复配置。

### 5.3 请求头

```
Authorization: Bearer <token>
```

`app.py` 增加：

- `_bearer_token(self) -> str | None`
- `_auth_project(self, project_name: str) -> bool`：校验 token 存在且 `resolve_token(project)` 与 `project_name` 一致；失败 `_send_json(..., 401|403)` 并返回 False。
- `_auth_any(self) -> bool`：仅校验 token 有效（用于无 project 字段的工具 API）。

### 5.4 路由保护

**公开（无需 token）：**

| 路由 | 说明 |
|------|------|
| `GET /`、`GET /index.html` | 页面与门禁 UI |
| `POST /api/project-unlock` | 登录 |
| `GET /fetch-url` | 外链抓取 |
| `GET /api/layout-extend/presets` | 通用模板 |

**需 token 且 project 一致：**

| 路由 | project 来源 |
|------|----------------|
| `GET /projects` | 仅返回 token 对应单项（防泄露另一项目元数据） |
| `GET /projects/{p}/images` | path |
| `GET /projects/{p}/images/{file}` | path |
| `GET /projects/{p}/types/{type}/{file}` | path |
| `GET /api/design-types` | query `project` |
| `GET /api/output-sizes` | query `project`（当带 project 时） |
| `GET /history` | 过滤：`entry.project == token.project`；无 `project` 的旧条目不展示 |
| `POST /parse`、`POST /api/analyze` | form `project` |
| `POST /generate-variants`、`POST /generate-with-prompt` | form `project` |
| `POST /api/generation/jobs`、`GET /api/generation/jobs*` | payload / 任务内 project |
| `POST /api/multi-size-export`、`POST /api/layout-extend` | form `project` |
| `POST /api/smart-cutout` 等带 `project` 的生图类接口 | form `project` |
| `GET /api/system-info` | 使用 token 对应项目的 Lovart Key 做可达性探测 |

**需 token、不校验 project 字段：**

| 路由 |
|------|
| `POST /api/crop-image` |
| `POST /api/magic-cutout` |
| `POST /api/gif-to-svga` |
| `POST /api/make-breathing-gif` |

**暂不保护：**

| 路由 | 说明 |
|------|------|
| `GET /outputs/{file}` | v1 保持现状 |

### 5.5 历史记录

- 所有 `build_history_entry` / `add_history` 写入点增加 `project` 字段（当前解锁的项目名）。
- `GET /history` 在 token 校验后只返回 `project` 匹配的条目。

### 5.6 项目组隔离的大模型 Key（新增）

#### 5.6.1 背景

当前 `app.py` 使用**全局**环境变量：`DEEPSEEK_API_KEY`、`LOVART_ACCESS_KEY` 等；`load_lovart_credentials()` 读取一组 Key 并在额度不足时轮换。所有项目组共用同一套 Key，无法实现品牌侧独立计费/配额/账号。

门禁锁定项目组后，所有 AI 调用应使用 **该组专属 Key**；token 校验通过的 `project` 是选 Key 的唯一依据（不信任客户端提交的 `project` 字段与 token 不一致的情况，已在 5.3 拦截）。

#### 5.6.2 项目组后缀

与密码、product_type 一致，环境变量后缀固定为：

| 本地项目名 | 后缀 |
|------------|------|
| 画啦啦 | `HLL` |
| 小灯塔 | `XDT` |

`project_auth.project_slug("画啦啦")` → `"HLL"`（集中映射，避免散落字符串）。

#### 5.6.3 新模块 `backend/project_credentials.py`

```python
@dataclass
class ProjectLlmConfig:
    slug: str                      # HLL | XDT
    project: str                   # 画啦啦 | 小灯塔
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    qianwen_api_key: str
    # ... qianwen_base_url, qianwen_model
    kimi_api_key: str
    doubao_api_key: str
    doubao_vision_model: str
    lovart_credentials: list[tuple[str, str]]  # 每组仅本项目的 AK/SK；可选 _2 为同组备用
    lovart_base_url: str

def get_project_llm_config(project: str) -> ProjectLlmConfig: ...
def load_lovart_credentials_for_project(project: str) -> list[tuple[str, str]]: ...
def require_project_llm_config(project: str) -> ProjectLlmConfig:
    """未配置必填 Key 时抛 ProjectCredentialsError，供 API 返回 503 + 明确文案。"""
```

**环境变量命名**（`BASE_URL` / `MODEL` / `LOVART_BASE_URL` 等可共用全局一份；**API Key 必须带 `_HLL` / `_XDT` 后缀**）：

```bash
# ── 画啦啦（HLL）──
DEEPSEEK_API_KEY_HLL=sk-user-...
DEEPSEEK_BASE_URL=https://agenthub.vipthink.cn   # 可与 XDT 共用，或设 DEEPSEEK_BASE_URL_HLL
DEEPSEEK_MODEL=claude-haiku-4-5-20251001         # 或与 DEEPSEEK_MODEL_HLL
LOVART_ACCESS_KEY_HLL=ak_...                     # 原「主 Key」
LOVART_SECRET_KEY_HLL=sk_...
# 可选：同组第二备用 LOVART_ACCESS_KEY_HLL_2（仅画啦啦额度不足时轮换，不与小灯塔共用）

# ── 小灯塔（XDT）──
DEEPSEEK_API_KEY_XDT=sk-user-...
LOVART_ACCESS_KEY_XDT=ak_...                     # 原「备用 Key _2」
LOVART_SECRET_KEY_XDT=sk_...
```

`load_lovart_credentials_for_project(project)`：只读取 `LOVART_*_{slug}` 与 `LOVART_*_{slug}_2`…，**不**读取 `LOVART_ACCESS_KEY`、`LOVART_ACCESS_KEY_2` 等无后缀变量。

#### 5.6.4 严格分组（禁止全局 Key 回落）

| 规则 | 说明 |
|------|------|
| **禁止跨项目** | 小灯塔会话绝不读取 `*_HLL`；画啦啦绝不读取 `*_XDT` |
| **禁止无后缀回落** | 不读取 `DEEPSEEK_API_KEY`、`LOVART_ACCESS_KEY`、`LOVART_ACCESS_KEY_2` 等旧变量参与业务请求 |
| **缺配置即失败** | 例如画啦啦生图未设 `LOVART_ACCESS_KEY_HLL` → `503`，文案：`画啦啦未配置 Lovart Key（LOVART_ACCESS_KEY_HLL）` |
| **废弃旧变量** | 实现完成后 `.env` 删除无后缀 Lovart/DeepSeek Key；`.env.example` 仅展示 `_HLL` / `_XDT` 写法 |

实现示例（无 `or` 回落）：

```python
key = os.environ.get(f"DEEPSEEK_API_KEY_{slug}", "").strip()
if not key:
    raise ProjectCredentialsError(f"{project} 未配置 DEEPSEEK_API_KEY_{slug}")
```

#### 5.6.5 当前仓库 Key 分配（迁移对照）

现网 `.env` 有两组 Lovart AK/SK、一组 AgentHub（DeepSeek 兼容）Key。实现时按下列**一次性**迁移（值从现有无后缀变量拷贝，不在代码里写死）：

| 现变量（将废弃） | 分配给 | 新变量 |
|------------------|--------|--------|
| `LOVART_ACCESS_KEY` + `LOVART_SECRET_KEY` | **画啦啦** | `LOVART_ACCESS_KEY_HLL` + `LOVART_SECRET_KEY_HLL` |
| `LOVART_ACCESS_KEY_2` + `LOVART_SECRET_KEY_2` | **小灯塔** | `LOVART_ACCESS_KEY_XDT` + `LOVART_SECRET_KEY_XDT` |
| `DEEPSEEK_API_KEY`（仅 1 个） | **两组各一份** | `DEEPSEEK_API_KEY_HLL` 与 `DEEPSEEK_API_KEY_XDT` 均填**当前同一 sk**；日后 AgentHub 若发放第二个 Key，只替换其中一侧 |

说明：

- 两把 Lovart Key **一一对应**两个品牌，不再在同一项目内自动轮换另一品牌的 Key。
- 若某组需 Lovart 备用 Key，仅在该组后缀下配置 `_HLL_2` / `_XDT_2`，不得把另一组的 Key 当作备用。
- `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`LOVART_BASE_URL` 等可继续用全局单份（非密钥、不涉计费隔离）。

#### 5.6.6 调用链改造

1. **鉴权后取配置**  
   Handler 通过 `_auth_project` 得到 `auth_project`，调用 `cfg = get_project_llm_config(auth_project)`。

2. **文本润色**  
   - `analyze_prompt_from_summary(..., project: str)` 增加 `project` 参数。  
   - `call_deepseek` / `call_qianwen` / `call_kimi` / `call_doubao` 必须传入 `config: ProjectLlmConfig`（由 `require_project_llm_config` 提供，无全局默认）。  
   - `_handle_analyze`、`_handle_parse` 等：token 校验后 `project = auth_project`，忽略或校验 form 中的 `project` 与 token 一致。

3. **Lovart 生图**  
   - `call_lovart(..., local_project=...)` 内部改为 `credentials = load_lovart_credentials_for_project(local_project)`，不再使用模块级 `LOVART_CREDENTIALS`。  
   - `ensure_lovart_project`、`generate_variants`、`lovart_queue` 任务执行：一律从 `payload["project"]` 解析配置。  
   - `check_lovart_reachable`：改为 `check_lovart_reachable(project)`，供需 token 的探测接口使用。

4. **异步任务**  
   `build_generation_payload` 已含 `project`；队列 worker 执行生图/润色时用该 `project` 加载 `ProjectLlmConfig`，与创建任务时的 token 项目一致（创建任务接口已校验 token）。

5. **解锁响应（可选）**  
   `POST /api/project-unlock` 200 可增加非敏感字段，便于运维自检：

   ```json
   "credentials_status": {
     "deepseek": true,
     "lovart": true,
     "qianwen": false
   }
   ```

   不返回 Key 内容。

#### 5.6.7 与 `_reload_runtime_env` 的关系

- `_reload_runtime_env()` 可继续加载全局 `DEEPSEEK_BASE_URL`、`LOVART_BASE_URL` 等非密钥项。  
- **不再**维护模块级 `LOVART_CREDENTIALS` / `DEEPSEEK_API_KEY` 全局密钥；生图与润色一律 `get_project_llm_config(project)`。  
- 每次请求从 `os.environ` 读取带后缀 Key（支持 `dev.sh` 热更新 `.env`）。

#### 5.6.8 配置与文档

- `.env.example`：删除无后缀 `LOVART_ACCESS_KEY` 示例；改为 `_HLL` / `_XDT` 两套，并注释上表迁移关系。  
- `ENVIRONMENT.md`：说明「必须分组配置、禁止回落」及 Lovart 主 Key→画啦啦、备用 Key→小灯塔的约定。

## 6. 前端设计

### 6.1 状态

```javascript
var projectAuthToken = null;  // 仅内存，不写 localStorage / sessionStorage / Cookie
```

### 6.2 `authFetch(url, options)`

- 若有 `projectAuthToken`，设置 `Authorization: Bearer ...`。
- `401`：清空 token、`showProjectGate('登录已失效，请重新输入密码')`。
- `403`：提示无权访问该项目。

将 `index.html` 内约 22 处项目相关 `fetch` 改为 `authFetch`（含轮询、下载 blob）。

### 6.3 初始化

```
window.onload → showProjectGate()
用户提交 → POST /api/project-unlock
成功 → projectAuthToken = data.token; hide gate; initUnlockedApp(data)
```

`initUnlockedApp`：

- `applyUnlockedProject(data)`：填充只读项目组，设置 `currentProjectCatalog` 等。
- `loadDesignTypesForProject`、`loadHistory`、`loadMultiSizePresets` 等（原 onload 逻辑）。

不再在未解锁时调用 `GET /projects`。

### 6.4 错误文案

| 场景 | 文案 |
|------|------|
| 密码错误 | 密码错误 |
| 未选项目 | 请选择项目组 |
| 网络失败 | 网络错误，请重试 |
| 未配置密码 | 该项目组未配置密码，请联系管理员 |
| 未配置该项目 Key | 画啦啦未配置 Lovart Key（LOVART_ACCESS_KEY_HLL）等，按变量名提示 |

## 7. 配置与文档

- `.env.example`：增加 `PROJECT_PASSWORD_HLL`、`PROJECT_PASSWORD_XDT`；增加 `DEEPSEEK_API_KEY_HLL` / `_XDT`、`LOVART_ACCESS_KEY_HLL` / `_XDT` 等示例与注释。
- `ENVIRONMENT.md`：新增「项目组门禁密码」「项目组大模型 Key」两小节。

## 8. 测试

新文件 `backend/tests/test_project_auth.py`：

| 用例 | 预期 |
|------|------|
| 正确密码 `unlock` | 返回 token，`resolve_token` 匹配 project |
| 错误密码 | `None` |
| 未知项目组 | 拒绝 |
| 未配置密码的环境变量 | 明确失败 |
| token 与请求 project 不一致 | 403（handler 或集成测） |
| 无 token 访问 `/api/design-types` | 401 |

新文件 `backend/tests/test_project_credentials.py`：

| 用例 | 预期 |
|------|------|
| 设置 `DEEPSEEK_API_KEY_HLL` / `DEEPSEEK_API_KEY_XDT` 不同值 | `get_project_llm_config` 分别返回对应 Key |
| 仅配置全局 `DEEPSEEK_API_KEY`、未配置 `_HLL` | `require_project_llm_config("画啦啦")` 抛错 |
| `load_lovart_credentials_for_project("小灯塔")` | 仅含 `*_XDT`，不含 `LOVART_ACCESS_KEY` / `*_HLL` |
| 仅配置 `LOVART_ACCESS_KEY_HLL` | 画啦啦返回 1 组；小灯塔返回空并触发未配置错误 |

运行：`python -m unittest backend.tests.test_project_auth backend.tests.test_project_credentials`

## 9. 实现文件清单

| 文件 | 操作 |
|------|------|
| `backend/project_auth.py` | 新建 |
| `backend/project_credentials.py` | 新建：按项目加载 LLM / Lovart 配置 |
| `backend/lovart_client.py` | `load_lovart_credentials(project_slug=None)` 或委托 project_credentials |
| `backend/app.py` | unlock、鉴权、各 handler 校验；`call_*` / `analyze_*` / `call_lovart` 传入 project 配置 |
| `backend/templates/index.html` | 门禁 UI、`authFetch`、初始化调整 |
| `.env.example`、`ENVIRONMENT.md` | 文档 |
| `backend/tests/test_project_auth.py` | 新建 |
| `backend/tests/test_project_credentials.py` | 新建 |

## 10. 验收标准

1. 打开首页：仅见门禁弹框，主界面不可操作。
2. 画啦啦 + 错误密码：无法进入，API 返回 401。
3. 画啦啦 + `hll2026`：进入后顶部固定画啦啦，可加载设计类型与参考图。
4. 直接 `curl` 带另一项目名的 API（同 token）：403。
5. F5 刷新：再次弹出门禁，需重新输入密码。
6. `?type=xdt`：弹框预选小灯塔，仍需 `xdt2026` 才能进入。
7. 小灯塔历史记录在画啦啦会话中不可见（有 `project` 字段的新记录）。
8. `.env` 中仅为画啦啦配置 `LOVART_ACCESS_KEY_HLL`、仅为小灯塔配置 `LOVART_ACCESS_KEY_XDT` 时：分别解锁后生图走各自 Lovart 账号（可通过 Lovart 控制台或日志中 `mask_access_key` 区分）。
9. 画啦啦会话下 `/api/analyze` **仅**使用 `DEEPSEEK_API_KEY_HLL`；删除全局 `DEEPSEEK_API_KEY` 后画啦啦仍可用、小灯塔须单独配置 `_XDT`。
10. 删除无后缀 `LOVART_ACCESS_KEY` 后：画啦啦生图日志中的 masked AK 以 `ak_5179…` 为主 Key 对应值；小灯塔为 `ak_852f…`（原 `_2`），二者不混用。
