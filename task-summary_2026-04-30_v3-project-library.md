# AI 设计修改助手 v3 - 项目风格库功能上线

## 目标
为工具增加项目风格库功能：选择项目 → 展示该项目的参考图 → 生成时传给即梦作为风格参考。

## 完成内容
1. **项目风格库**
   - 后端 API：`/projects`（列表）、`/projects/<name>/images`（图片列表）、`/projects/<name>/images/<file>`（图片文件）
   - 前端：下拉选择项目 → 网格展示参考图（最多选10张）→ 选中传给生成

2. **技术实现**
   - 即梦 `image2image` 支持 `--images` 接受逗号分隔的多张图片
   - 参考图与用户上传图一起传给即梦，AI 会参考风格

3. **使用方法**
   - 在 `~/projects/ai-design-modifier/projects/` 下创建项目文件夹
   - 把项目的设计图放进对应文件夹
   - 工具中选择项目，点击选中参考图，生成时自动带上

## 文件
- `~/projects/ai-design-modifier/backend/app.py` — v3 完整代码
- `~/projects/ai-design-modifier/projects/` — 项目风格库目录

## 后续优化建议
- 支持在工具内直接上传参考图到项目
- 参考图标签分类
- 风格强度调节参数
