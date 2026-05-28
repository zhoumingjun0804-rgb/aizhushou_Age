# History Record Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generation/edit history entries carry complete, consistent metadata (title/description/tags) and render it in the drawer without breaking old history data.

**Architecture:** Add a single history-entry builder in `backend/app.py`, route all history writes through it, and normalize legacy entries on read. Update the drawer renderer in `backend/templates/index.html` to display `title`, optional `description`, and tags with compact one-line truncation.

**Tech Stack:** Python (`http.server` backend), vanilla JS + HTML/CSS frontend, JSON file persistence (`history.json`).

---

## File Structure And Responsibilities

- Modify: `backend/app.py`
  - Add helper functions for title/tags/entry construction.
  - Update all history write call sites to use one builder.
  - Normalize missing fields for legacy entries in read path.
- Modify: `backend/templates/index.html`
  - Adjust history card rendering to prioritize `title` and show optional `description`.
  - Keep existing image preview/download interactions unchanged.
- Verify: `history.json` (runtime artifact, no manual editing required)
  - Confirm newly written entries include new fields.

### Task 1: Add Backend History Entry Builders

**Files:**
- Modify: `backend/app.py`
- Test: manual API flows (no dedicated pytest file currently for history formatting)

- [ ] **Step 1: Write a failing ad-hoc check script command (expected fail before code)**

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path("history.json")
if not p.exists():
    print("SKIP:no-history-file")
    raise SystemExit(0)
items = json.loads(p.read_text("utf-8"))
if not items:
    print("SKIP:empty-history")
    raise SystemExit(0)
sample = items[0]
required = ["title", "description", "meta_tags"]
missing = [k for k in required if k not in sample]
assert not missing, f"missing fields: {missing}"
print("OK")
PY
```

- [ ] **Step 2: Run the check and verify failure or skip**

Run: command above  
Expected: `AssertionError` for missing fields (or `SKIP` if no local history yet).

- [ ] **Step 3: Implement minimal backend helpers**

```python
# in backend/app.py, near history helpers
def _history_title_from_prompt(prompt: str, limit: int = 28) -> str:
    text = (prompt or "").strip()
    if not text:
        return "未命名记录"
    return text[:limit] + ("..." if len(text) > limit else "")

def _history_mode_label(mode: str) -> str:
    return "✏️局部修图" if mode == "edit" else ("✨文字生图" if mode == "text2img" else "📷图片改图")

def _history_meta_tags(mode: str, variants_count: int = 0) -> list:
    tags = [_history_mode_label(mode)]
    if int(variants_count or 0) > 1:
        tags.append(f"{int(variants_count)}张")
    return tags

def build_history_entry(*, mode: str, prompt: str, description: str = "", source: str = "", **kwargs) -> dict:
    entry = {
        "id": kwargs.get("id") or uuid.uuid4().hex[:8],
        "timestamp": kwargs.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "prompt": prompt or "",
        "title": _history_title_from_prompt(prompt or ""),
        "description": (description or "").strip(),
        "meta_tags": _history_meta_tags(mode, kwargs.get("variants_count", 0)),
        "source": source or "",
        "schema_version": 1,
    }
    entry.update({k: v for k, v in kwargs.items() if v is not None and v != ""})
    return entry
```

- [ ] **Step 4: Normalize legacy fields in read path**

```python
# in filter_history_items(items)
entry["title"] = entry.get("title") or _history_title_from_prompt(entry.get("prompt", ""))
entry["description"] = (entry.get("description") or "").strip()
if not isinstance(entry.get("meta_tags"), list) or not entry.get("meta_tags"):
    entry["meta_tags"] = _history_meta_tags(entry.get("mode", ""), entry.get("variants_count", 0))
entry["schema_version"] = entry.get("schema_version") or 1
```

- [ ] **Step 5: Run quick syntax verification**

Run: `python -m py_compile backend/app.py`  
Expected: no output, exit code 0.

- [ ] **Step 6: Commit backend helper changes**

```bash
git add backend/app.py
git commit -m "feat: standardize history entry metadata and legacy normalization"
```

### Task 2: Route All History Writes Through Unified Builder

**Files:**
- Modify: `backend/app.py` (generation, jobs, edit write points)
- Test: generation/edit API manual checks

- [ ] **Step 1: Write failing search check (expected old direct dict writes)**

Run: `rg "entry = \{" backend/app.py`  
Expected: matches at history write sections.

- [ ] **Step 2: Replace generation write point (`_handle_generate_variants` path)**

```python
# replace direct dict with builder call
entry = build_history_entry(
    mode=mode,
    prompt=prompt,
    description="",  # fill if available from summary in this path
    source="generate",
    project=project,
    input_image=input_filename,
    output_images=output_images,
    variants_count=len(output_images),
)
add_history(entry)
```

- [ ] **Step 3: Replace async job completion write point (`execute_generation_job`)**

```python
entry = build_history_entry(
    mode=mode,
    prompt=prompt,
    description="",
    source="job",
    project=project or "",
    input_image=input_filename,
    output_images=output_images,
    variants_count=len(output_images),
)
add_history(entry)
```

- [ ] **Step 4: Replace edit write point with description mapping**

```python
entry = build_history_entry(
    mode="edit",
    prompt=prompt,
    description=description,  # raw edit description required by spec
    source="edit",
    input_image=input_filename,
    output_image=output_filename,
    edit_type=edit_type,
)
add_history(entry)
```

- [ ] **Step 5: Run targeted functional smoke checks**

Run:
- `python -m py_compile backend/app.py`
- start server and run one generate + one edit from UI
- `python - <<'PY'\nimport json; d=json.load(open("history.json")); print(d[0].get("title"), d[0].get("description"), d[0].get("meta_tags"))\nPY`

Expected:
- compile passes
- new entries include `title`, `description`, `meta_tags`
- edit entry `description` equals user input text.

- [ ] **Step 6: Commit unified write routing**

```bash
git add backend/app.py
git commit -m "refactor: route history writes through shared builder"
```

### Task 3: Update History Drawer Rendering

**Files:**
- Modify: `backend/templates/index.html`
- Test: manual browser verification

- [ ] **Step 1: Add/adjust CSS for title/description one-line truncation**

```css
.history-title {
  font-size: 13px;
  color: rgba(255,255,255,0.9);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.history-desc {
  font-size: 12px;
  color: rgba(255,255,255,0.62);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

- [ ] **Step 2: Update render function to use standardized fields**

```javascript
var title = item.title || (item.prompt || '').substring(0, 28);
var description = (item.description || '').trim();
var tags = Array.isArray(item.meta_tags) ? item.meta_tags.join(' · ') : '';

var descHtml = description
  ? '<div class="history-desc" title="' + description.replace(/"/g, '&quot;') + '">' + description + '</div>'
  : '';

return '<div class="history-item"' + clickAttr + '>' +
  /* image block unchanged */ +
  '<div class="history-info">' +
  '<div class="history-time">' + time + '</div>' +
  '<div class="history-title">' + title + '</div>' +
  descHtml +
  '<div class="history-meta">' + tags + '</div>' +
  '</div></div>';
```

- [ ] **Step 3: Preserve legacy fallback behavior**

```javascript
// Keep existing mode/variants fallback if meta_tags absent.
if (!tags) {
  var mode = item.mode === 'edit' ? '✏️局部修图' : (item.mode === 'text2img' ? '✨文字生图' : '📷图片改图');
  var variants = item.variants_count > 1 ? (' · ' + item.variants_count + '张') : '';
  tags = mode + variants;
}
```

- [ ] **Step 4: Run manual UI checks**

Run:
- open history drawer after one generate + one edit
- verify card layout: time / title / optional description / tags
- click preview and download still works

Expected:
- no JS console error
- legacy items without `description` render without blank row.

- [ ] **Step 5: Commit frontend history card changes**

```bash
git add backend/templates/index.html
git commit -m "feat: show title and description in history drawer cards"
```

### Task 4: Final Regression And Documentation Sync

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-history-record-completeness-design.md` (only if behavior changed)
- Verify: `backend/app.py`, `backend/templates/index.html`

- [ ] **Step 1: Run final regression commands**

Run:
- `python -m py_compile backend/app.py`
- smoke test `/history` response from browser devtools or curl

Expected:
- compile success
- `/history` item payloads consistently include `title`, `description`, `meta_tags`, `schema_version`.

- [ ] **Step 2: Confirm spec-plan alignment**

Run: `rg "title|description|meta_tags|schema_version|source" docs/superpowers/specs/2026-05-28-history-record-completeness-design.md`  
Expected: all required terms present.

- [ ] **Step 3: Commit final integration pass**

```bash
git add backend/app.py backend/templates/index.html docs/superpowers/specs/2026-05-28-history-record-completeness-design.md
git commit -m "chore: finalize history completeness rollout verification"
```
