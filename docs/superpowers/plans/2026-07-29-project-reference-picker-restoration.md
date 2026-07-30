# 项目参考图库完整恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 恢复生图页从 `projects/` 勾选参考图（小灯塔直接展示；画啦啦先选设计类型），并保留本地上传。

**Architecture:** 前端恢复 `projectRefsSection` 可见性与 `shouldShowProjectRefs()`；画啦啦显示设计类型下拉，未选类型时不请求类型图片；后端沿用现有目录解析与 `selected_project_images` 注入。用静态模板断言 + `product_design` / 路径解析单测做回归。

**Tech Stack:** Python 3.10+、`unittest`、内嵌前端 `backend/templates/index.html`

**Spec:** `docs/superpowers/specs/2026-07-29-project-reference-picker-restoration-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/tests/test_project_refs_ui.py` | Create | 静态断言：图库不再强制隐藏；画啦啦设计类型逻辑；失败提示文案 |
| `backend/tests/test_project_reference_listing.py` | Create | 小灯塔扁平列表 / 画啦啦类型列表 / 勾选路径解析 |
| `backend/templates/index.html` | Modify | 恢复图库 UI、可见性逻辑、设计类型控件、加载失败提示 |
| `backend/product_design.py` | Verify | 一般无需改；若测试暴露缺口再最小修补 |
| `backend/app.py` | Verify | `get_project_images` / `_build_image_paths_from_selection` 已符合规格 |

---

### Task 1: 前端静态回归测试（先写失败用例）

**Files:**
- Create: `backend/tests/test_project_refs_ui.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_project_refs_ui.py
import re
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


class ProjectRefsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_project_refs_section_not_feature_hidden(self):
        m = re.search(
            r'<div[^>]*id="projectRefsSection"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(m)
        self.assertNotIn("feature-hidden", m.group(0))

    def test_should_show_project_refs_not_hardcoded_false(self):
        m = re.search(
            r"function shouldShowProjectRefs\(\)\s*\{(?P<body>.*?)\n\}",
            self.html,
            re.S,
        )
        self.assertIsNotNone(m)
        body = m.group("body")
        self.assertNotRegex(body, r"return\s+false\s*;")
        self.assertIn("getSelectedProjectName", body)

    def test_design_type_control_restored_for_folder_types(self):
        self.assertIn('id="designType"', self.html)
        self.assertIn("onDesignTypeChange", self.html)
        self.assertIn("updateDesignTypeVisibility", self.html)
        self.assertIn("folder_types", self.html)

    def test_folder_types_require_design_type_before_load(self):
        # selectProject / load path must gate on designType for folder_types
        self.assertRegex(
            self.html,
            r"folder_types[\s\S]{0,400}请选择设计类型",
        )

    def test_load_project_images_shows_user_facing_error(self):
        self.assertIn("参考图加载失败，请刷新后重试", self.html)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_project_refs_ui -v
```

Expected: 至少 `test_project_refs_section_not_feature_hidden`、`test_should_show_project_refs_not_hardcoded_false`、`test_load_project_images_shows_user_facing_error` 失败。

---

### Task 2: 后端参考图列表与勾选路径测试

**Files:**
- Create: `backend/tests/test_project_reference_listing.py`
- Modify if needed: `backend/tests/test_image_path_selection.py`

- [ ] **Step 1: 写失败/规格测试**

用临时目录隔离，避免依赖仓库里真实大图：

```python
# backend/tests/test_project_reference_listing.py
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import product_design
from app import _build_image_paths_from_selection


class ProjectReferenceListingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        xdt = self.root / "小灯塔"
        xdt.mkdir()
        (xdt / "project.json").write_text(
            json.dumps({"catalog": "static_types", "product_type": "xdt"}),
            encoding="utf-8",
        )
        (xdt / "poster.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        hll = self.root / "画啦啦"
        (hll / "types" / "17-封面").mkdir(parents=True)
        (hll / "project.json").write_text(
            json.dumps({"catalog": "folder_types", "product_type": "hll"}),
            encoding="utf-8",
        )
        (hll / "types" / "17-封面" / "cover.jpg").write_bytes(b"JPEGFAKE")

        self._projects_patch = patch.object(product_design, "PROJECTS_DIR", self.root)
        self._projects_patch.start()
        self.addCleanup(self._projects_patch.stop)

    def test_list_flat_images_for_xdt_root(self):
        names = product_design.list_flat_reference_images("小灯塔")
        self.assertEqual(names, ["poster.png"])

    def test_list_typed_images_for_hll(self):
        names = product_design.list_typed_reference_images("画啦啦", "17-封面")
        self.assertEqual(names, ["cover.jpg"])

    def test_selected_xdt_image_resolves(self):
        fields = {
            "selected_project_images": json.dumps(["小灯塔/poster.png"]),
        }
        paths = _build_image_paths_from_selection(fields, "小灯塔")
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertEqual(paths[0].name, "poster.png")

    def test_selected_hll_typed_image_resolves(self):
        fields = {
            "selected_project_images": json.dumps(
                ["画啦啦/types/17-封面/cover.jpg"]
            ),
        }
        paths = _build_image_paths_from_selection(fields, "画啦啦")
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertEqual(paths[0].name, "cover.jpg")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_project_reference_listing -v
```

Expected: 若 `PROJECTS_DIR` patch 与现有 `resolve_reference_image_path` 一致则应 PASS；若因 `app` 导入时缓存路径失败，再按实际错误最小修补测试（优先 patch `product_design.PROJECTS_DIR`，必要时同步 patch `app` 里复用的同名引用）。**不要为了过测试改生产解析语义。**

若本任务测试已绿，可跳过实现改动，直接进入 Task 3。

---

### Task 3: 恢复 HTML 结构（去掉隐藏、恢复设计类型条）

**Files:**
- Modify: `backend/templates/index.html`（`#projectRefsSection` 附近）

- [ ] **Step 1: 替换项目参考区 markup**

将：

```html
<div class="project-section feature-hidden" id="projectRefsSection" aria-hidden="true">
    <select id="designType" style="display:none;" aria-hidden="true">
        <option value="">请选择设计类型</option>
    </select>
    <p class="project-refs-hint" id="projectRefsHint">请先选择项目组，再挑选参考图（也可在下方自行上传）</p>
    <div class="project-refs-wrap" id="projectRefsWrap" style="display: none;">
        <div class="project-info" id="projectInfo"></div>
        <div class="project-images-grid" id="projectImagesGrid"></div>
    </div>
</div>
```

改为：

```html
<div class="project-section" id="projectRefsSection" style="display:none;" aria-hidden="true">
    <div class="project-bar" id="designTypeBar" style="display:none;margin-bottom:8px;">
        <div class="project-bar-field">
            <label for="designType">设计类型</label>
            <select id="designType" onchange="onDesignTypeChange()">
                <option value="">请选择设计类型</option>
            </select>
        </div>
    </div>
    <p class="project-refs-hint" id="projectRefsHint">请先选择项目组，再挑选参考图（也可在下方自行上传）</p>
    <div class="project-refs-wrap" id="projectRefsWrap" style="display: none;">
        <div class="project-info" id="projectInfo"></div>
        <div class="project-images-grid" id="projectImagesGrid"></div>
    </div>
</div>
```

- [ ] **Step 2: 跑静态测试，确认 section 相关用例推进**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_project_refs_ui.ProjectRefsUiTests.test_project_refs_section_not_feature_hidden -v
```

Expected: PASS

---

### Task 4: 恢复可见性与画啦啦设计类型逻辑

**Files:**
- Modify: `backend/templates/index.html`（`shouldShowProjectRefs` / `updateProjectRefsVisibility` / `selectProject` / `loadProjectImages`）

- [ ] **Step 1: 替换 `shouldShowProjectRefs` 与可见性函数**

```javascript
function shouldShowProjectRefs() {
    return !!getSelectedProjectName();
}

function updateDesignTypeVisibility() {
    var bar = document.getElementById('designTypeBar');
    var sel = document.getElementById('designType');
    if (!bar || !sel) return;
    var show = shouldShowProjectRefs() && currentProjectCatalog === 'folder_types';
    bar.style.display = show ? 'flex' : 'none';
    sel.setAttribute('aria-hidden', show ? 'false' : 'true');
    if (!show) {
        sel.value = '';
    }
}

function updateProjectRefsVisibility() {
    var show = shouldShowProjectRefs();
    var section = document.getElementById('projectRefsSection');
    var wrap = document.getElementById('projectRefsWrap');
    var hint = document.getElementById('projectRefsHint');
    if (section) {
        section.style.display = show ? 'block' : 'none';
        section.setAttribute('aria-hidden', show ? 'false' : 'true');
    }
    updateDesignTypeVisibility();
    if (wrap) wrap.style.display = show ? 'block' : 'none';
    if (hint) {
        if (!show) {
            hint.style.display = 'block';
            hint.textContent = '请先选择项目组，再挑选参考图（也可在下方自行上传）';
        } else if (currentProjectCatalog === 'folder_types' && !(document.getElementById('designType') || {}).value) {
            hint.style.display = 'block';
            hint.textContent = '请选择设计类型后挑选参考图（也可在下方自行上传）';
        } else {
            hint.style.display = 'none';
        }
    }
    if (!show) clearProjectRefs();
}
```

- [ ] **Step 2: 更新 `selectProject`，画啦啦未选类型时不请求图片**

在 `selectProject()` 内，设置 `currentProject` / `projectInfo` 之后、调用 `loadProjectImages` 之前插入：

```javascript
    updateDesignTypeVisibility();
    if (currentProjectCatalog === 'folder_types' && !designType) {
        selectedProjectImages = [];
        if (grid) {
            grid.innerHTML = '<span class="select-hint">请选择设计类型后挑选参考图</span>';
            grid.classList.add('active');
        }
        return;
    }
    loadProjectImages(meta.name, designType, grid);
```

并删除原来无条件的 `loadProjectImages(...)` 调用（避免双调用）。

- [ ] **Step 3: `onDesignTypeChange` 清空项目勾选后重载（不清空本地上传）**

```javascript
function onDesignTypeChange() {
    selectedProjectImages = [];
    applyDefaultSizeForDesignType();
    updateProjectRefsVisibility();
    onProjectOrDesignTypeChange();
}
```

确认 `onProjectSelectChange` 仍会 `clearUploadedRefImages()`；`onDesignTypeChange` **不要**调用它。

- [ ] **Step 4: `loadProjectImages` catch 展示用户可见错误**

将 catch 改为：

```javascript
    } catch(e) {
        console.error('加载项目图片失败', e);
        if (grid) {
            grid.innerHTML = '<span class="select-hint">参考图加载失败，请刷新后重试</span>';
            grid.classList.add('active');
        }
    }
```

- [ ] **Step 5: 跑静态测试全绿**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_project_refs_ui -v
```

Expected: 全部 PASS

---

### Task 5: 端到端核对与回归

**Files:**
- Verify only（必要时小修 `index.html` / 测试）

- [ ] **Step 1: 跑相关单测**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest \
  tests.test_project_refs_ui \
  tests.test_project_reference_listing \
  tests.test_image_path_selection \
  tests.test_ref_image_upload \
  -v
```

Expected: 全部 PASS

- [ ] **Step 2: 手工检查清单（浏览器）**

1. 选「小灯塔」→ 出现项目参考图网格（根目录图片可见）。
2. 勾选 ≤10 张，本地再上传 1 张，生图请求带上两者。
3. 选「画啦啦」→ 出现设计类型下拉，默认「请选择设计类型」，网格提示先选类型。
4. 选「17-封面」→ 加载该目录图片；切换类型后勾选清空，本地上传仍在。
5. 刷新后行为一致。

- [ ] **Step 3: 提交（仅当用户明确要求 commit 时）**

```bash
git add \
  backend/tests/test_project_refs_ui.py \
  backend/tests/test_project_reference_listing.py \
  backend/templates/index.html \
  docs/superpowers/specs/2026-07-29-project-reference-picker-restoration-design.md \
  docs/superpowers/plans/2026-07-29-project-reference-picker-restoration.md
git commit -m "$(cat <<'EOF'
feat: 恢复生图页项目参考图库选择

EOF
)"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| 页面内嵌图库在上传区上方 | Task 3（现有 DOM 位置不变） |
| 小灯塔选组后直接加载 | Task 4 `shouldShow` + `selectProject` |
| 画啦啦先选设计类型再加载 | Task 4 gate + `updateDesignTypeVisibility` |
| 最多 10 张 / 预览 | 已有 `toggleProjectImage` / preview；Task 1 不回归破坏 |
| 项目图 + 本地图同时提交 | 已有 `selected_project_images` + `ref_image_*`；Task 5 手测 |
| 切组/切类型清空项目勾选 | Task 4；切组清空本地上传、切类型不清理本地 |
| 空目录 / 加载失败提示 | Task 4 catch + 既有 empty hint |
| 静态 + 后端列表/路径测试 | Task 1–2、5 |

## Self-review notes

- 无 TBD/占位步骤。
- `updateDesignTypeVisibility` 在 Task 1 静态测试与 Task 4 实现中同名一致。
- 不引入弹窗/搜索/分页；不改本地上传上限 3。
