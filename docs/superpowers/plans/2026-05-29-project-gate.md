# 项目组门禁 + 分组 Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打开/刷新页面需选项目组并输入密码；后端 Bearer token 锁定项目；画啦啦与小灯塔使用各自 Lovart/LLM Key（主 Key→HLL，原 `_2`→XDT），禁止无后缀全局 Key 回落。

**Architecture:** `project_auth.py` 管密码与内存 token；`project_credentials.py` 按 `HLL`/`XDT` 后缀读 `.env`；`app.py` Handler 统一 `_auth_project`/`_auth_any`；`index.html` 全屏门禁 + `authFetch`。Lovart 生图/润色从 token 绑定 `project` 取配置，不再用 `LOVART_CREDENTIALS` 全局列表。

**Tech Stack:** Python 3.10+、`http.server`、原生 JS、`unittest`、现有 `lovart_client` / `lovart_queue`。

**Spec:** `docs/superpowers/specs/2026-05-29-project-gate-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/project_auth.py` | Create | 密码、token、slug 映射 |
| `backend/project_credentials.py` | Create | 按项目 LLM/Lovart 配置，严格无回落 |
| `backend/tests/test_project_auth.py` | Create | 门禁单元测试 |
| `backend/tests/test_project_credentials.py` | Create | 分组 Key 单元测试 |
| `backend/lovart_client.py` | Modify | 可选：`load_lovart_credentials(slug=...)` 委托 credentials |
| `backend/app.py` | Modify | unlock、鉴权、路由保护、LLM/Lovart 按 project |
| `backend/templates/index.html` | Modify | 门禁 UI、`authFetch`、初始化 |
| `.env.example` | Modify | `_HLL`/`_XDT` Key + 门禁密码 |
| `ENVIRONMENT.md`, `AGENTS.md` | Modify | 配置说明 |
| `.env`（本地，不提交） | Manual | 按迁移表拆分 Key |

---

### Task 0: 本地 `.env` 迁移（实现前）

**Files:**
- Modify: `.env`（仅本机，勿提交密钥）

- [ ] **Step 1: 复制 Lovart Key**

```bash
# 画啦啦 ← 原主 Key
LOVART_ACCESS_KEY_HLL=<原 LOVART_ACCESS_KEY>
LOVART_SECRET_KEY_HLL=<原 LOVART_SECRET_KEY>

# 小灯塔 ← 原 _2
LOVART_ACCESS_KEY_XDT=<原 LOVART_ACCESS_KEY_2>
LOVART_SECRET_KEY_XDT=<原 LOVART_SECRET_KEY_2>
```

- [ ] **Step 2: 复制 DeepSeek Key（当前仅 1 个，两组各一份）**

```bash
DEEPSEEK_API_KEY_HLL=<原 DEEPSEEK_API_KEY>
DEEPSEEK_API_KEY_XDT=<原 DEEPSEEK_API_KEY>
# DEEPSEEK_BASE_URL / DEEPSEEK_MODEL 可保留全局
```

- [ ] **Step 3: 门禁密码**

```bash
PROJECT_PASSWORD_HLL=hll2026
PROJECT_PASSWORD_XDT=xdt2026
```

- [ ] **Step 4: 实现通过验收后删除旧变量**

删除：`LOVART_ACCESS_KEY`、`LOVART_SECRET_KEY`、`LOVART_ACCESS_KEY_2`、`LOVART_SECRET_KEY_2`、`DEEPSEEK_API_KEY`（确认代码已不再读取）。

---

### Task 1: `project_auth` 模块

**Files:**
- Create: `backend/project_auth.py`
- Create: `backend/tests/test_project_auth.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_project_auth.py
import os
import unittest
from unittest.mock import patch

from project_auth import unlock, resolve_token, project_slug, ALLOWED_PROJECTS


class ProjectAuthTests(unittest.TestCase):
    def setUp(self):
        project_auth = __import__("project_auth")
        project_auth._tokens.clear()

    def test_project_slug(self):
        self.assertEqual(project_slug("画啦啦"), "HLL")
        self.assertEqual(project_slug("小灯塔"), "XDT")

    @patch.dict(os.environ, {"PROJECT_PASSWORD_HLL": "secret-hll"}, clear=False)
    def test_unlock_success(self):
        token = unlock("画啦啦", "secret-hll")
        self.assertTrue(token)
        info = resolve_token(token)
        self.assertEqual(info["project"], "画啦啦")

    @patch.dict(os.environ, {"PROJECT_PASSWORD_HLL": "secret-hll"}, clear=False)
    def test_unlock_wrong_password(self):
        self.assertIsNone(unlock("画啦啦", "wrong"))

    def test_unlock_unknown_project(self):
        self.assertIsNone(unlock("不存在", "x"))
```

- [ ] **Step 2: 运行确认 FAIL**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_project_auth -v
```

Expected: `ModuleNotFoundError: project_auth`

- [ ] **Step 3: 实现 `project_auth.py`**

```python
# backend/project_auth.py
"""项目组门禁：密码校验 + 内存 token。"""
from __future__ import annotations

import os
import secrets
import time
import uuid
from typing import Optional

TOKEN_TTL_SECONDS = 12 * 3600

ALLOWED_PROJECTS = ("画啦啦", "小灯塔")
_PROJECT_SLUG = {"画啦啦": "HLL", "小灯塔": "XDT"}
_PASSWORD_ENV = {"画啦啦": "PROJECT_PASSWORD_HLL", "小灯塔": "PROJECT_PASSWORD_XDT"}

_tokens: dict[str, dict] = {}


def project_slug(project: str) -> str:
    slug = _PROJECT_SLUG.get(project)
    if not slug:
        raise ValueError(f"unknown project: {project}")
    return slug


def _password_for(project: str) -> Optional[str]:
    env_name = _PASSWORD_ENV.get(project)
    if not env_name:
        return None
    value = os.environ.get(env_name, "").strip()
    return value or None


def unlock(project: str, password: str) -> Optional[str]:
    if project not in ALLOWED_PROJECTS:
        return None
    expected = _password_for(project)
    if not expected:
        return None
    if not secrets.compare_digest(password, expected):
        return None
    token = uuid.uuid4().hex
    _tokens[token] = {"project": project, "created_at": time.time()}
    return token


def resolve_token(token: str) -> Optional[dict]:
    if not token:
        return None
    entry = _tokens.get(token)
    if not entry:
        return None
    if time.time() - entry["created_at"] > TOKEN_TTL_SECONDS:
        _tokens.pop(token, None)
        return None
    return {"project": entry["project"]}
```

- [ ] **Step 4: 运行测试 PASS**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_project_auth -v
```

---

### Task 2: `project_credentials` 模块

**Files:**
- Create: `backend/project_credentials.py`
- Create: `backend/tests/test_project_credentials.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_project_credentials.py
import os
import unittest
from unittest.mock import patch

from project_credentials import (
    ProjectCredentialsError,
    load_lovart_credentials_for_project,
    require_project_llm_config,
)


class ProjectCredentialsTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "LOVART_ACCESS_KEY_HLL": "ak_hll",
            "LOVART_SECRET_KEY_HLL": "sk_hll",
            "LOVART_ACCESS_KEY_XDT": "ak_xdt",
            "LOVART_SECRET_KEY_XDT": "sk_xdt",
            "DEEPSEEK_API_KEY_HLL": "ds_hll",
            "DEEPSEEK_API_KEY_XDT": "ds_xdt",
            "DEEPSEEK_BASE_URL": "https://example.com",
            "DEEPSEEK_MODEL": "m1",
            "LOVART_BASE_URL": "https://lgw.lovart.ai",
        },
        clear=False,
    )
    def test_lovart_per_project(self):
        hll = load_lovart_credentials_for_project("画啦啦")
        xdt = load_lovart_credentials_for_project("小灯塔")
        self.assertEqual(hll, [("ak_hll", "sk_hll")])
        self.assertEqual(xdt, [("ak_xdt", "sk_xdt")])

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "global-only"}, clear=False)
    def test_no_global_fallback(self):
        for key in list(os.environ):
            if key.endswith("_HLL") or key.endswith("_XDT"):
                os.environ.pop(key, None)
        with self.assertRaises(ProjectCredentialsError):
            require_project_llm_config("画啦啦")
```

- [ ] **Step 2: 运行确认 FAIL**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_project_credentials -v
```

- [ ] **Step 3: 实现 `project_credentials.py`**

要点：

- `load_lovart_credentials_for_project(project)`：读 `LOVART_ACCESS_KEY_{slug}`、`LOVART_SECRET_KEY_{slug}` 及 `{slug}_2`…；**不**读无后缀 `LOVART_ACCESS_KEY`。
- `require_project_llm_config(project)`：`DEEPSEEK_API_KEY_{slug}` 必填；`deepseek_base_url` = `os.environ.get(f"DEEPSEEK_BASE_URL_{slug}") or os.environ.get("DEEPSEEK_BASE_URL")`（URL 可回落，Key 不可）。
- `credentials_status(project) -> dict[str, bool]`：供 unlock 响应，检查 `bool(deepseek_api_key)`、`bool(lovart_credentials)`。

- [ ] **Step 4: 运行测试 PASS**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_project_credentials -v
```

---

### Task 3: `app.py` — unlock API 与鉴权辅助

**Files:**
- Modify: `backend/app.py`（`Handler` 类附近）

- [ ] **Step 1: 顶部 import**

```python
from project_auth import unlock, resolve_token, ALLOWED_PROJECTS
from project_credentials import (
    ProjectCredentialsError,
    require_project_llm_config,
    credentials_status,
)
```

- [ ] **Step 2: 在 `Handler` 内增加辅助方法**

```python
def _read_json_body(self) -> dict:
    length = int(self.headers.get("Content-Length", 0) or 0)
    raw = self.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}

def _bearer_token(self) -> str:
    auth = (self.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""

def _token_project(self) -> str | None:
    info = resolve_token(self._bearer_token())
    return info["project"] if info else None

def _auth_any(self) -> bool:
    if self._token_project():
        return True
    self._send_json({"error": "未登录或登录已失效"}, status=401)
    return False

def _auth_project(self, project_name: str) -> str | None:
    """校验 token；成功返回 auth_project 名，失败已写响应。"""
    project_name = (project_name or "").strip()
    auth = self._token_project()
    if not auth:
        self._send_json({"error": "未登录或登录已失效"}, status=401)
        return None
    if project_name and project_name != auth:
        self._send_json({"error": "无权访问该项目"}, status=403)
        return None
    return auth
```

- [ ] **Step 3: `POST /api/project-unlock`**

在 `do_POST` 的 `post_routes` 增加 `'/api/project-unlock': self._handle_project_unlock`。

```python
def _handle_project_unlock(self):
    _reload_runtime_env()
    body = self._read_json_body()
    project = (body.get("project") or "").strip()
    password = body.get("password") or ""
    if project not in ALLOWED_PROJECTS:
        self._send_json({"error": "未知项目组"}, status=400)
        return
    from project_auth import _password_for  # 或导出 is_password_configured
    if not _password_for(project):
        self._send_json({"error": "该项目组未配置密码"}, status=400)
        return
    token = unlock(project, password)
    if not token:
        self._send_json({"error": "密码错误"}, status=401)
        return
    meta = get_project_meta(project) or {}
    self._send_json({
        "token": token,
        "project": project,
        "display_name": meta.get("display_name") or project,
        "catalog": detect_project_catalog(project),
        "product_type": project_product_type(project),
        "credentials_status": credentials_status(project),
    })
```

- [ ] **Step 4: 手动 curl 验证**

```bash
curl -s -X POST http://127.0.0.1:8040/api/project-unlock \
  -H 'Content-Type: application/json' \
  -d '{"project":"画啦啦","password":"hll2026"}' | python -m json.tool
```

Expected: `token` 字段存在。

---

### Task 4: `app.py` — LLM / Lovart 按 project 取 Key

**Files:**
- Modify: `backend/app.py`（`call_deepseek`、`call_lovart`、`analyze_prompt_from_summary` 等）

- [ ] **Step 1: 改 `call_deepseek` 签名（示例，千问/ Kimi / 豆包同理）**

```python
def call_deepseek(messages, config, temperature=0.7, max_tokens=1000):
    if not config.deepseek_api_key:
        return None, f"未配置 DEEPSEEK_API_KEY_{config.slug}"
    headers = {
        "Authorization": f"Bearer {config.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.deepseek_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{config.deepseek_base_url.rstrip('/')}/v1/chat/completions",
        ...
    )
```

- [ ] **Step 2: `analyze_prompt_from_summary` 增加 `project` 参数**

```python
def analyze_prompt_from_summary(summary, project_meta=None, regenerate=False, project: str = ""):
    cfg = require_project_llm_config(project)
    ...
    ai_prompt, error = call_deepseek(messages, cfg, temperature=temperature, max_tokens=500)
```

- [ ] **Step 3: `call_lovart` 使用 `load_lovart_credentials_for_project(local_project)`**

替换 `LOVART_CREDENTIALS` 循环；`local_project` 为空时返回未配置错误。

- [ ] **Step 4: `check_lovart_reachable(project: str)`**

```python
def check_lovart_reachable(project: str, timeout: int = 8) -> tuple[bool, str]:
    try:
        creds = load_lovart_credentials_for_project(project)
    except ProjectCredentialsError as e:
        return False, str(e)
    if not creds:
        return False, f"{project} 未配置 Lovart Key"
    ak, sk = creds[0]
    ...
```

- [ ] **Step 5: 更新 `_handle_analyze` / `_handle_parse`**

```python
auth_project = self._auth_project(fields.get("project", ""))
if not auth_project:
    return
try:
    cfg = require_project_llm_config(auth_project)
except ProjectCredentialsError as e:
    self._send_json({"error": str(e)}, status=503)
    return
ai_prompt, ... = analyze_prompt_from_summary(..., project=auth_project)
```

- [ ] **Step 6: `_reload_runtime_env` 移除全局 `LOVART_CREDENTIALS` / `DEEPSEEK_API_KEY` 赋值**（或保留仅用于启动日志的废弃警告，不参与业务）。

- [ ] **Step 7: 运行已有测试 + 新测试**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest discover -s tests -v
```

---

### Task 5: `app.py` — 保护所有路由 + history

**Files:**
- Modify: `backend/app.py`（`do_GET` / `do_POST` 各 handler）

- [ ] **Step 1: `GET /projects`**

```python
auth = self._token_project()
if not auth:
    self._send_json({"error": "未登录"}, status=401)
    return
all_projects = list_projects()
one = [p for p in all_projects if p["name"] == auth]
self._send_json({"projects": one})
```

- [ ] **Step 2: 项目 path 路由**（`/projects/{p}/images` 等）

在解析 `project` 后：`if self._auth_project(project) is None: return`。

- [ ] **Step 3: `GET /history`**

```python
auth = self._token_project()
if not auth:
    self._send_json({"error": "未登录"}, status=401)
    return
items = [i for i in filter_history_items(load_history()) if i.get("project") == auth]
self._send_json({"items": items})
```

- [ ] **Step 4: 所有 `add_history` / `build_history_entry` 写入 `project=`**

检查 `execute_generation_job`（已有）、`_handle_generate_variants`、edit 等入口，统一 `project=auth_project`。

- [ ] **Step 5: 工具类 POST**（crop/gif 等）

handler 开头：`if not self._auth_any(): return`。

- [ ] **Step 6: `_handle_system_info`**

```python
auth = self._token_project()
if not auth:
    self._send_json({"error": "未登录"}, status=401)
    return
ok, msg = check_lovart_reachable(auth)
self._send_json({
    "project": auth,
    "lovartReachable": ok,
    "lovartMessage": msg,
    "lovartKeyCount": len(load_lovart_credentials_for_project(auth)),
    ...
})
```

- [ ] **Step 7: 无 token 访问受保护 API 应 401**

```bash
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8040/api/design-types?project=画啦啦"
```

Expected: `401`

---

### Task 6: 前端门禁 + `authFetch`

**Files:**
- Modify: `backend/templates/index.html`

- [ ] **Step 1: HTML — 遮罩与主容器**

在 `<body>` 内最前增加：

```html
<div id="projectGateOverlay" class="project-gate-overlay">
  <div class="project-gate-card">
    <h2>选择项目组</h2>
    <label>项目组</label>
    <select id="gateProjectSelect">
      <option value="">请选择</option>
      <option value="画啦啦">画啦啦</option>
      <option value="小灯塔">小灯塔</option>
    </select>
    <label>密码</label>
    <input type="password" id="gatePassword" autocomplete="current-password" />
    <p id="gateError" class="gate-error"></p>
    <button type="button" id="gateSubmitBtn">进入</button>
  </div>
</div>
<div id="appMain"> <!-- 现有页面内容包进此 div --> </div>
```

补充 CSS：全屏 `position:fixed; inset:0; z-index:9999`，与现有紫色暗色主题一致。

- [ ] **Step 2: JS 状态与 `authFetch`**

```javascript
var projectAuthToken = null;
var unlockedProjectMeta = null;

function authFetch(url, options) {
  options = options || {};
  var headers = new Headers(options.headers || {});
  if (projectAuthToken) {
    headers.set('Authorization', 'Bearer ' + projectAuthToken);
  }
  options.headers = headers;
  return fetch(url, options).then(function(res) {
    if (res.status === 401) {
      projectAuthToken = null;
      showProjectGate('登录已失效，请重新输入密码');
      return Promise.reject(new Error('unauthorized'));
    }
    return res;
  });
}
```

- [ ] **Step 3: `showProjectGate` / `submitProjectGate`**

- 打开时：`defaultProjectFromUrl()` 写入 `#gateProjectSelect`。
- 提交：`POST /api/project-unlock`（普通 `fetch`，无需 token）。
- 成功：`projectAuthToken = data.token`；`hide overlay`；`initUnlockedApp(data)`。

- [ ] **Step 4: 修改 `window.onload`**

```javascript
window.onload = function() {
  initImageBackendSelect();
  showProjectGate();
};
function initUnlockedApp(data) {
  applyUnlockedProject(data);
  loadDesignTypesForProject(data.project);
  loadHistory();
  ...
}
function applyUnlockedProject(data) {
  var sel = document.getElementById('projectSelect');
  sel.innerHTML = '';
  var opt = document.createElement('option');
  opt.value = data.project;
  opt.textContent = data.display_name;
  opt.dataset.catalog = data.catalog;
  opt.dataset.productType = data.product_type;
  sel.appendChild(opt);
  sel.disabled = true;
  currentProjectCatalog = data.catalog;
  applyProductBranding();
}
```

- [ ] **Step 5: 全局替换**

将业务 `fetch(` 改为 `authFetch(`（`/api/project-unlock` 与静态资源除外）。可用编辑器批量替换后人工检查。

- [ ] **Step 6: 浏览器验收**

1. 刷新 → 仅见门禁  
2. 画啦啦 + `hll2026` → 进入  
3. `#projectSelect` 不可切换  
4. F5 → 再次门禁  

---

### Task 7: `.env.example` 与文档

**Files:**
- Modify: `.env.example`
- Modify: `ENVIRONMENT.md`
- Modify: `AGENTS.md`（简短一句门禁说明）

- [ ] **Step 1: `.env.example`**

- 删除无后缀 `LOVART_ACCESS_KEY` 作为主配置示例。
- 增加 `PROJECT_PASSWORD_HLL` / `PROJECT_PASSWORD_XDT`。
- 增加 `LOVART_*_HLL`、`LOVART_*_XDT`、`DEEPSEEK_API_KEY_HLL` / `_XDT` 及迁移注释（主 Key→画啦啦，_2→小灯塔）。

- [ ] **Step 2: `ENVIRONMENT.md`**

新增两节：「项目组门禁密码」「项目组大模型 Key（禁止全局回落）」。

- [ ] **Step 3: `AGENTS.md`**

在 HTTP API 表增加 `POST /api/project-unlock`；注明需 `Authorization: Bearer`。

---

### Task 8: 端到端验收（spec §10）

- [ ] 打开首页仅门禁；错误密码 401  
- [ ] 画啦啦解锁后可加载 design-types / 参考图  
- [ ] 同 token 访问另一 project 的 API → 403  
- [ ] F5 重新门禁  
- [ ] `?type=xdt` 预选仍要密码  
- [ ] 历史按 project 过滤  
- [ ] 生图日志 masked AK：画啦啦 `ak_5179…`、小灯塔 `ak_852f…`（与迁移后 env 一致）  
- [ ] 删除无后缀 Key 后两组仍各自可用  

---

## Spec coverage self-review

| Spec 章节 | Task |
|-----------|------|
| 门禁 UX | Task 6 |
| Token / 路由保护 | Task 3, 5 |
| 分组 Key 严格无回落 | Task 2, 4, 0 |
| Key 迁移 HLL/XDT | Task 0, 7 |
| history project | Task 5 |
| 测试 | Task 1, 2 |
| 文档 | Task 7 |

## Placeholder scan

无 TBD；各 Task 含可执行命令与代码骨架。
