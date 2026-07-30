# 固定项目组实例隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `remote` 部署的小灯塔实例和 `remote-hll` 部署的画啦啦实例分别锁定项目组，隐藏项目切换栏，并在前后端阻止跨项目访问。

**Architecture:** 部署脚本继续向两个目录同步同一份代码和本机 `.env`，随后只修改目标目录的远端 `.env`，写入对应 `FIXED_PROJECT`。后端把该变量作为实例级可信配置，过滤项目列表和历史、校验项目请求，并向 HTML 注入标题及隐藏样式；现有前端项目初始化逻辑会从过滤后的单项目列表自动加载对应参考素材。

**Tech Stack:** Bash、Python 3.10+、`unittest`、原生 HTML/JavaScript

**Spec:** `docs/superpowers/specs/2026-07-29-remote-hll-deploy-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/tests/test_fixed_project_instances.py` | Create | 固定项目配置、HTML 注入、请求隔离回归测试 |
| `backend/tests/test_deploy_instance_profiles.py` | Create | 两种远端部署 profile 的静态回归测试 |
| `backend/project_auth.py` | Modify | 读取并校验 `FIXED_PROJECT` |
| `backend/app.py` | Modify | 强制项目、过滤项目数据、注入实例品牌 |
| `backend/templates/index.html` | Modify | 接收标题、品牌和项目切换栏隐藏占位符 |
| `deploy.sh` | Modify | 为两个远端目录写入对应实例配置 |
| `README.md` | Modify | 说明实例锁定效果 |
| `ENVIRONMENT.md` | Modify | 记录 `FIXED_PROJECT` 与部署行为 |

---

### Task 1: 固定项目配置与页面品牌

**Files:**
- Create: `backend/tests/test_fixed_project_instances.py`
- Modify: `backend/project_auth.py`
- Modify: `backend/app.py`
- Modify: `backend/templates/index.html`

- [ ] **Step 1: 写失败测试**

创建测试，覆盖合法/非法环境值以及 HTML 注入：

```python
import os
import unittest
from unittest.mock import patch

import app
from project_auth import fixed_project


class FixedProjectConfigTests(unittest.TestCase):
    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_fixed_project_accepts_known_project(self):
        self.assertEqual(fixed_project(), "画啦啦")

    @patch.dict(os.environ, {"FIXED_PROJECT": "未知项目"}, clear=False)
    def test_fixed_project_rejects_unknown_project(self):
        self.assertIsNone(fixed_project())

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_html_uses_fixed_project_brand_and_hides_picker(self):
        html = (
            "<title>__PAGE_TITLE__</title>"
            "<h1>__PAGE_BRAND__</h1>"
            '<div class="shared-project-card__PROJECT_CARD_EXTRA__"></div>'
        )
        rendered = app._inject_instance_flags(html)
        self.assertIn("<title>A-智绘 · 画啦啦</title>", rendered)
        self.assertIn("<h1>🎨 A-智绘 · 画啦啦</h1>", rendered)
        self.assertIn("shared-project-card feature-hidden", rendered)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd backend && python3 -m unittest tests.test_fixed_project_instances -v
```

Expected: FAIL，因为 `fixed_project` 和 `_inject_instance_flags` 尚不存在。

- [ ] **Step 3: 实现最小配置读取**

在 `backend/project_auth.py` 增加：

```python
def fixed_project() -> Optional[str]:
    value = os.environ.get("FIXED_PROJECT", "").strip()
    return value if value in ALLOWED_PROJECTS else None
```

在 `backend/app.py` 导入 `fixed_project`，并将页面注入函数调整为：

```python
def _inject_instance_flags(html: str) -> str:
    gate_on = is_gate_enabled()
    locked = fixed_project() or ""
    html = html.replace("__PROJECT_GATE_ENABLED__", "true" if gate_on else "false")
    html = html.replace("__GATE_OVERLAY_EXTRA__", "" if gate_on else " hidden")
    html = html.replace("__APP_MAIN_EXTRA__", " app-locked" if gate_on else "")
    html = html.replace("__PAGE_TITLE__", f"A-智绘 · {locked}" if locked else "A-智绘")
    html = html.replace("__PAGE_BRAND__", f"🎨 A-智绘 · {locked}" if locked else "🎨 A-智绘")
    html = html.replace("__PROJECT_CARD_EXTRA__", " feature-hidden" if locked else "")
    return html
```

让 `get_html_page()` 的三个返回分支都调用 `_inject_instance_flags()`。

在 `backend/templates/index.html` 替换三个静态位置：

```html
<title>__PAGE_TITLE__</title>
<h1>__PAGE_BRAND__</h1>
<div class="card shared-project-card__PROJECT_CARD_EXTRA__">
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
cd backend && python3 -m unittest tests.test_fixed_project_instances -v
```

Expected: 3 tests PASS。

---

### Task 2: 后端项目数据与请求隔离

**Files:**
- Modify: `backend/tests/test_fixed_project_instances.py`
- Modify: `backend/app.py`

- [ ] **Step 1: 增加失败测试**

向测试文件增加：

```python
class FixedProjectRequestTests(unittest.TestCase):
    def make_handler(self):
        handler = app.Handler.__new__(app.Handler)
        handler.sent = None
        handler._send_json = lambda payload, status=200: setattr(
            handler, "sent", (payload, status)
        )
        return handler

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_matching_project_is_allowed(self):
        handler = self.make_handler()
        self.assertEqual(handler._auth_project("画啦啦"), "画啦啦")
        self.assertIsNone(handler.sent)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_cross_project_request_is_forbidden(self):
        handler = self.make_handler()
        self.assertIsNone(handler._auth_project("小灯塔"))
        self.assertEqual(handler.sent[1], 403)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_missing_project_resolves_to_fixed_project(self):
        handler = self.make_handler()
        handler._query_params = lambda: {}
        self.assertEqual(handler._resolve_project_for_request(""), "画啦啦")

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_filter_keeps_only_fixed_project_items(self):
        items = [{"project": "小灯塔"}, {"project": "画啦啦"}]
        self.assertEqual(
            app._filter_for_fixed_project(items),
            [{"project": "画啦啦"}],
        )
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd backend && python3 -m unittest tests.test_fixed_project_instances.FixedProjectRequestTests -v
```

Expected: FAIL，跨项目请求仍被接受，且过滤函数不存在。

- [ ] **Step 3: 实现强制项目和数据过滤**

在 `backend/app.py` 增加：

```python
def _filter_for_fixed_project(items: list[dict], key: str = "project") -> list[dict]:
    locked = fixed_project()
    if not locked:
        return items
    return [item for item in items if item.get(key) == locked]
```

在 `_resolve_project_for_request()` 最前面返回固定项目；若请求明确携带另一项目，则交由 `_auth_project()` 返回 `403`。在 `_auth_project()` 最前面加入：

```python
locked = fixed_project()
if locked:
    if project_name and project_name.strip() != locked:
        self._send_json({"error": f"本实例仅支持项目组「{locked}」"}, status=403)
        return None
    return locked
```

修改 GET 路由：

```python
# /projects
self._send_json({"projects": _filter_for_fixed_project(list_projects(), key="name")})

# /history
items = _filter_for_fixed_project(filter_history_items(load_history()))
self._send_json({"items": items})
```

`/api/output-sizes` 未带项目参数时，使用 `fixed_project()` 对应的 `project_product_type()`；图片、设计类型等已有项目参数的路由继续通过 `_auth_project()` 拒绝跨项目访问。

- [ ] **Step 4: 运行固定项目测试**

Run:

```bash
cd backend && python3 -m unittest tests.test_fixed_project_instances -v
```

Expected: 全部 PASS。

---

### Task 3: 部署脚本写入实例配置

**Files:**
- Create: `backend/tests/test_deploy_instance_profiles.py`
- Modify: `deploy.sh`

- [ ] **Step 1: 写失败静态回归测试**

```python
import unittest
from pathlib import Path


DEPLOY = (Path(__file__).resolve().parents[2] / "deploy.sh").read_text(encoding="utf-8")


class DeployInstanceProfileTests(unittest.TestCase):
    def test_remote_locks_xdt(self):
        self.assertIn('REMOTE_FIXED_PROJECT="小灯塔"', DEPLOY)
        self.assertRegex(DEPLOY, r"cmd_remote\(\).*?apply_remote_xdt_profile")

    def test_remote_hll_locks_hll_and_port(self):
        self.assertIn('REMOTE_FIXED_PROJECT="画啦啦"', DEPLOY)
        self.assertIn('REMOTE_HINT_PORT="${REMOTE_PORT_HLL}"', DEPLOY)

    def test_profiles_reuse_dot_env(self):
        self.assertNotIn('REMOTE_ENV_SRC="$ROOT_DIR/.env.hll"', DEPLOY)
        self.assertIn("--exclude '.env.hll'", DEPLOY)

    def test_instance_values_are_written_before_deploy(self):
        self.assertIn('remote_set_env_kv "FIXED_PROJECT"', DEPLOY)
        self.assertRegex(
            DEPLOY,
            r"cmd_remote_deploy\(\).*?remote_apply_instance_env.*?remote_run_deploy",
        )
```

正则测试使用 `re.S` 编译，确保可跨行匹配函数体。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd backend && python3 -m unittest tests.test_deploy_instance_profiles -v
```

Expected: FAIL，因为部署 profile 尚未写入 `FIXED_PROJECT`。

- [ ] **Step 3: 最小修改部署脚本**

在全局变量中增加：

```bash
REMOTE_FIXED_PROJECT=""
```

将 `remote_set_port()` 泛化为：

```bash
remote_set_env_kv() {
  local key="$1" val="$2"
  info "远端设置 ${key}=${val}（仅改 ${REMOTE_DIR}/.env）..."
  remote_ssh "cd '${REMOTE_DIR}' && \
    if grep -qE '^[[:space:]]*${key}=' .env; then
      sed -i.bak -E 's/^[[:space:]]*${key}=.*/${key}=${val}/' .env && rm -f .env.bak
    else
      printf '\\n%s=%s\\n' '${key}' '${val}' >> .env
    fi"
}
```

保留 `remote_set_port()` 作为对 `remote_set_env_kv "PORT"` 的薄封装。新增 `apply_remote_xdt_profile()`，让 `cmd_remote()` 调用它；`apply_remote_hll_profile()` 设置画啦啦。新增：

```bash
remote_apply_instance_env() {
  if [ -n "${REMOTE_HINT_PORT}" ]; then
    remote_set_port "${REMOTE_HINT_PORT}"
  fi
  remote_set_env_kv "FIXED_PROJECT" "${REMOTE_FIXED_PROJECT}"
}
```

`cmd_remote_sync()` 和 `cmd_remote_deploy()` 在 rsync 后调用该函数；`remote_rsync()` 排除本机 `.env.hll`，但继续同步 `.env`。

- [ ] **Step 4: 运行部署脚本测试和语法检查**

Run:

```bash
cd backend && python3 -m unittest tests.test_deploy_instance_profiles -v
cd .. && bash -n deploy.sh
```

Expected: 测试全部 PASS，`bash -n` exit 0。

---

### Task 4: 文档与完整回归

**Files:**
- Modify: `README.md`
- Modify: `ENVIRONMENT.md`

- [ ] **Step 1: 更新部署说明**

在两份文档的远端部署表中明确：

```text
remote：远端写 FIXED_PROJECT=小灯塔，只显示小灯塔
remote-hll：远端写 FIXED_PROJECT=画啦啦 和 PORT=8629，只显示画啦啦
两个命令共用本机 .env，不读取 .env.hll
```

- [ ] **Step 2: 运行相关测试**

Run:

```bash
cd backend && python3 -m unittest \
  tests.test_fixed_project_instances \
  tests.test_deploy_instance_profiles \
  tests.test_project_auth \
  tests.test_project_refs_ui \
  tests.test_project_reference_listing -v
```

Expected: 全部 PASS。

- [ ] **Step 3: 运行完整后端测试**

Run:

```bash
cd backend && python3 -m unittest discover -s tests -v
```

Expected: 全部 PASS；若存在与本次改动无关的既有失败，单独记录，不扩大修复范围。

- [ ] **Step 4: 本地实例配置冒烟检查**

Run:

```bash
cd backend && FIXED_PROJECT=画啦啦 python3 -c \
  "import app; html=app.get_html_page(); assert 'A-智绘 · 画啦啦' in html; assert 'shared-project-card feature-hidden' in html"
```

Expected: exit 0。

部署到测试机后人工验收：

```bash
./deploy.sh remote
./deploy.sh remote-hll
```

分别确认小灯塔实例标题为“小灯塔”、画啦啦实例标题为“画啦啦”、两边都不显示项目切换栏，且参考图库属于对应项目。

> 不创建 Git commit；只有用户明确要求时再提交。
