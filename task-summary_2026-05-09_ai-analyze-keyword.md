# AI 视觉设计助手 - 关键词分析功能上线

**时间**: 2026-05-09 00:20 GMT+8

## 目标
在生成图片前，先显示 AI 分析的关键词，让用户可以预览和修改后再生成。

## 实现方案

### 前端修改
1. **新增「🔍 AI 分析关键词」按钮**
   - 蓝色渐变背景，点击后调用 `/api/analyze` 接口
   - 分析成功后隐藏自身，显示关键词区域

2. **新增关键词展示区域**
   - 浅蓝色背景卡片，包含：
     - 标题 + 提示文字
     - 可编辑的 textarea
     - 「🎨 生成 3 张变体」按钮
   - 用户可以修改 AI 生成的关键词后再生成图片

### 后端修改
1. **新增 `/api/analyze` 路由**
   - 接收表单数据（设计类型、主标题、副标题等）
   - 调用 `build_prompt_from_summary()` 生成关键词
   - 只返回 prompt，不调用即梦 API

2. **新增 `/generate-with-prompt` 路由**
   - 接收用户修改后的 prompt
   - 直接调用即梦生成图片
   - 支持项目组参考图自动注入

## 新流程
```
填写需求表单
    ↓
点击「🔍 AI 分析关键词」
    ↓
查看/修改关键词（textarea可编辑）
    ↓
点击「🎨 生成 3 张变体」
    ↓
选择满意的变体 → 下载/放大
```

## 技术细节
- 前端使用纯 JavaScript，无额外依赖
- 关键词区域默认隐藏，分析成功后显示
- 保留旧的 `generateVariants()` 函数兼容性
- 项目组风格标签自动注入到 prompt

## 文件修改
- `~/projects/ai-design-modifier/backend/app.py`
  - 添加 CSS 样式（`.analyze-btn`, `.keyword-section`, `.keyword-textarea`）
  - 添加 HTML 元素（分析按钮、关键词区域）
  - 添加 JS 函数（`analyzeKeyword()`, `generateWithKeyword()`）
  - 添加后端路由（`_handle_analyze`, `_handle_generate_with_prompt`）

## 后续优化建议
1. 关键词高亮显示（设计类型、标题、画面描述用不同颜色）
2. 添加"重新分析"按钮
3. 保存用户修改后的关键词作为偏好学习
