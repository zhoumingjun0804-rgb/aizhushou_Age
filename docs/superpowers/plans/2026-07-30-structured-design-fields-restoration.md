# 结构化设计需求字段恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将单一 `designBrief` 文本框局部回退为原有结构化字段，不影响当前尺寸、模型、画质、数量和参考图能力。

**Architecture:** 只修改 `backend/templates/index.html` 的需求表单和对应前端读取逻辑。恢复旧字段 ID 与 `buildDesignSummary()` 数据结构，后端请求格式保持不变；新增静态回归测试锁定字段、读取、变更重置和页面重置行为。

**Tech Stack:** HTML、原生 JavaScript、Python `unittest`

**Spec:** `docs/superpowers/specs/2026-07-30-structured-design-fields-restoration-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/tests/test_structured_design_form.py` | Create | 结构化表单及 JS 数据流静态回归测试 |
| `backend/templates/index.html` | Modify | 恢复字段 markup、风格逻辑、summary、重置逻辑 |

---

### Task 1: 添加结构化表单失败测试

**Files:**
- Create: `backend/tests/test_structured_design_form.py`

- [ ] **Step 1: 写静态回归测试**

```python
import re
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


def function_body(source: str, name: str) -> str:
    match = re.search(
        rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{(?P<body>.*?)\n\}}",
        source,
        re.S,
    )
    if not match:
        raise AssertionError(f"function {name} not found")
    return match.group("body")


class StructuredDesignFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_structured_fields_replace_design_brief(self):
        for field_id in (
            "requirementName", "mainTitle", "subTitle", "visualDesc",
            "styleSelect", "customStyle", "layoutRef", "extraNotes",
        ):
            self.assertRegex(self.html, rf'id="{field_id}"')
        self.assertNotRegex(self.html, r'id="designBrief"')

    def test_style_select_supports_custom_value(self):
        self.assertIn('value="custom"', self.html)
        self.assertIn("toggleCustomStyle()", self.html)
        self.assertIn("function getStyleValue()", self.html)

    def test_design_summary_reads_structured_fields(self):
        body = function_body(self.html, "buildDesignSummary")
        for field_id in ("mainTitle", "subTitle", "visualDesc", "layoutRef", "extraNotes"):
            self.assertIn(field_id, body)
        self.assertIn("getStyleValue()", body)
        self.assertNotIn("parseDesignBrief", body)

    def test_requirement_name_reads_its_own_field(self):
        body = function_body(self.html, "getRequirementName")
        self.assertIn("requirementName", body)
        self.assertNotIn("parseDesignBrief", body)

    def test_change_reset_binds_all_summary_fields(self):
        body = function_body(self.html, "bindDesignFormChangeReset")
        for field_id in (
            "mainTitle", "subTitle", "visualDesc", "layoutRef",
            "extraNotes", "styleSelect", "customStyle",
        ):
            self.assertIn(field_id, body)

    def test_reset_all_clears_structured_fields(self):
        body = function_body(self.html, "resetAll")
        for field_id in (
            "requirementName", "mainTitle", "subTitle", "visualDesc",
            "layoutRef", "extraNotes", "styleSelect", "customStyle",
        ):
            self.assertIn(field_id, body)
        self.assertNotIn("designBrief", body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行并确认红灯**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend
.venv/bin/python3 -m unittest tests.test_structured_design_form -v
```

Expected: 结构化字段、风格函数和 summary 读取相关断言失败；无导入或语法错误。

---

### Task 2: 恢复结构化字段 markup

**Files:**
- Modify: `backend/templates/index.html`

- [ ] **Step 1: 删除单一设计需求文本框**

移除：

```html
<div class="form-row">
    <div class="form-item full-width">
        <label>设计需求</label>
        <textarea id="designBrief" class="design-brief-textarea"></textarea>
        <p class="design-brief-hint">...</p>
    </div>
</div>
```

- [ ] **Step 2: 在分析按钮之前恢复字段**

```html
<div class="form-row">
    <div class="form-item full-width">
        <label>需求名称</label>
        <input type="text" id="requirementName" placeholder="用于下载文件名，如：暑期班开屏">
    </div>
</div>
<div class="form-row">
    <div class="form-item">
        <label>主标题</label>
        <input type="text" id="mainTitle" placeholder="如：暑期班火热招生中">
    </div>
    <div class="form-item">
        <label>副标题</label>
        <input type="text" id="subTitle" placeholder="如：限时优惠 前50名8折">
    </div>
</div>
<div class="form-row">
    <div class="form-item full-width">
        <label>画面描述</label>
        <input type="text" id="visualDesc" placeholder="如：蓝天白云，卡通儿童奔跑">
    </div>
</div>
<div class="form-row">
    <div class="form-item">
        <label>风格</label>
        <select id="styleSelect" onchange="toggleCustomStyle()">
            <option value="">默认</option>
            <option value="简约">简约</option>
            <option value="卡通">卡通</option>
            <option value="中国风">中国风</option>
            <option value="科技感">科技感</option>
            <option value="可爱">可爱</option>
            <option value="商务">商务</option>
            <option value="复古">复古</option>
            <option value="潮流">潮流</option>
            <option value="custom">自定义</option>
        </select>
    </div>
    <div class="form-item" id="customStyleInput" style="display:none;">
        <label>自定义风格</label>
        <input type="text" id="customStyle" placeholder="如：赛博朋克、国潮">
    </div>
</div>
<div class="form-row">
    <div class="form-item full-width">
        <label>排版参考</label>
        <input type="text" id="layoutRef" placeholder="如：标题居中顶部，正文底部左对齐">
    </div>
</div>
<div class="form-row">
    <div class="form-item full-width">
        <label>补充备注</label>
        <input type="text" id="extraNotes" placeholder="如：品牌色 #FF6B6B，LOGO 左上角">
    </div>
</div>
```

保持尺寸、数量、模型、GPT 画质、润色模型的现有 markup 和默认值不变。

- [ ] **Step 3: 运行字段存在测试**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend
.venv/bin/python3 -m unittest \
  tests.test_structured_design_form.StructuredDesignFormTests.test_structured_fields_replace_design_brief \
  -v
```

Expected: PASS

---

### Task 3: 恢复结构化字段 JavaScript 数据流

**Files:**
- Modify: `backend/templates/index.html`

- [ ] **Step 1: 恢复需求名称读取**

```javascript
function getRequirementName() {
    var el = document.getElementById('requirementName');
    return sanitizeDownloadNamePart(el && el.value);
}
```

删除不再使用的 `getDesignBriefText()` 与 `parseDesignBrief()`。

- [ ] **Step 2: 恢复风格函数**

```javascript
function toggleCustomStyle() {
    var sel = document.getElementById('styleSelect');
    var customInput = document.getElementById('customStyleInput');
    if (!sel || !customInput) return;
    customInput.style.display = sel.value === 'custom' ? 'block' : 'none';
}

function getStyleValue() {
    var sel = document.getElementById('styleSelect');
    if (!sel) return '';
    if (sel.value === 'custom') {
        var custom = document.getElementById('customStyle');
        return custom ? custom.value.trim() : '';
    }
    return sel.value;
}
```

- [ ] **Step 3: 恢复 summary**

```javascript
function buildDesignSummary() {
    return {
        '设计类型': getDesignTypeLabel(),
        '主标题': document.getElementById('mainTitle').value.trim(),
        '副标题': document.getElementById('subTitle').value.trim(),
        '画面描述': document.getElementById('visualDesc').value.trim(),
        '排版参考': document.getElementById('layoutRef').value.trim(),
        '风格': getStyleValue(),
        '补充备注': document.getElementById('extraNotes').value.trim()
    };
}
```

从 `buildPromptFromForm()` 删除 `parseDesignBrief()` 和 raw 文本特殊分支，保留后续由 summary 拼接 prompt 的逻辑。

- [ ] **Step 4: 恢复表单变更监听**

```javascript
function bindDesignFormChangeReset() {
    ['mainTitle', 'subTitle', 'visualDesc', 'layoutRef', 'extraNotes', 'styleSelect', 'customStyle'].forEach(function(id) {
        var el = document.getElementById(id);
        if (!el) return;
        ['input', 'change'].forEach(function(evt) {
            el.addEventListener(evt, function() {
                if (!keywordAnalysisReady) return;
                var fp = designSummaryFingerprint(buildDesignSummary());
                if (fp !== keywordAnalysisSnapshot) resetKeywordAnalysis();
            });
        });
    });
}
```

- [ ] **Step 5: 恢复页面重置**

```javascript
['requirementName', 'mainTitle', 'subTitle', 'visualDesc', 'layoutRef', 'extraNotes', 'customStyle'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = '';
});
var styleSelect = document.getElementById('styleSelect');
if (styleSelect) styleSelect.value = '';
toggleCustomStyle();
```

- [ ] **Step 6: 运行结构化表单测试**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend
.venv/bin/python3 -m unittest tests.test_structured_design_form -v
```

Expected: 全部 PASS

---

### Task 4: 回归验证

**Files:**
- Verify only

- [ ] **Step 1: 运行结构化表单和项目参考图库测试**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend
.venv/bin/python3 -m unittest \
  tests.test_structured_design_form \
  tests.test_project_refs_ui \
  tests.test_project_reference_listing \
  tests.test_image_path_selection \
  -v
```

Expected: 全部 PASS

- [ ] **Step 2: 页面手工检查**

1. 尺寸、数量、模型、GPT 画质和润色模型保持原值。
2. 输入主标题、画面描述、风格后，AI 分析和直接生成均可提交。
3. 选择“自定义风格”后文本框出现，切回预设后隐藏。
4. 修改任一需求字段会清空旧关键词分析结果。
5. 项目参考图仍可勾选并与结构化需求一起提交。

- [ ] **Step 3: 提交（仅用户明确要求时）**

不自动 commit；若用户明确要求，再按当前完整 diff 组织提交。

---

## Self-review

- 所有设计字段均有对应 markup、读取和重置步骤。
- 不改后端 API，summary key 与现有接口一致。
- 不改变尺寸、模型、画质、数量和参考图。
- 无 TBD、TODO 或未定义函数。
