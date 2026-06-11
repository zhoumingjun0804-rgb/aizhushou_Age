# 项目组目录结构

每个品牌/项目组一个子文件夹，通过 **项目组下拉框** 切换设计类型与参考图。

## 目录约定

```
projects/
  小灯塔/                    # static_types：固定设计类型列表
    project.json
    refs/                    # 可选；无 refs/ 时根目录图片亦作参考图
      *.png
    （或直接在根目录放参考图）

  画啦啦/                    # folder_types：设计类型 = types/ 下子文件夹
    project.json
    refs/                    # 可选：通用参考图（不参与按类型筛选）
    types/
      01-banner/
        *.jpg
      06-开屏/
        *.jpg
      16-小程序封面图/
        ...
```

## project.json 字段

| 字段 | 说明 |
|------|------|
| `catalog` | `static_types`（小灯塔）或 `folder_types`（画啦啦） |
| `product_type` | 开屏导出尺寸：`xdt` → `output_sizes.json`，`hll` → `output_sizes_hll.json` |
| `display_name` | 界面显示名 |
| `lovart_project_id` | Lovart 项目绑定（可选） |
| `lovart_project_title` | Lovart 文件夹名（可选；默认 `{display_name}-A智绘`，如小灯塔→`小灯塔-A智绘`） |

未写 `catalog` 时：若存在 `types/` 且含子文件夹 → 自动识别为 `folder_types`。

## 新增画啦啦设计类型

在 `projects/画啦啦/types/` 下新建 `序号-名称` 文件夹并放入参考图即可，例如 `17-新物料/`。

## 新增小灯塔类项目组

复制 `小灯塔/project.json`，将参考图放入 `refs/` 或项目组根目录。
