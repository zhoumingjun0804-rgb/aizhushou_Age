# AI 视觉设计助手（交付版）

与 `xiao-a-dengta` 同源分支：本仓库保留 **Lovart 多 Key 自动切换**、`ssl_utils` 等企业网络适配，并同步小A灯塔的辅助工具与多生图后端。

## 快速启动

```bash
cp .env.example .env
./dev.sh          # 开发（推荐）
./start.sh        # 生产
```

| 修改 | 操作 |
|------|------|
| `backend/templates/index.html` | 保存后浏览器刷新 |
| `backend/*.py` | `./dev.sh` 下约 1 秒自动重启 |

## 架构要点

- 入口：`backend/app.py`
- UI：`backend/templates/index.html`（不再内嵌于 app.py）
- 生图：`call_image_generator()` → Lovart（多 Key）/ 即梦 / ComfyUI / SD
- 工具模块：`gif_maker.py`、`image_crop.py`、`multi_size_export.py`、`gif_to_svga/`

## 项目组与设计类型

- **项目组下拉框** 决定设计类型列表与参考图目录（见 `projects/README.md`）
- `static_types`（小灯塔）：固定设计类型；参考图在 `projects/<组>/refs/` 或根目录
- `folder_types`（画啦啦）：设计类型 = `projects/画啦啦/types/<序号-名称>/`；开屏导出 6 尺寸
- 可选 URL `?project=画啦啦` 或 `?type=hll` 默认选中项目组

## HTTP API（相较旧版新增）

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/output-sizes?type=xdt\|hll` | 多尺寸导出预设（随 type 变化） |
| POST | `/api/multi-size-export` | 单图导出 9 种尺寸 |
| POST | `/api/crop-image` | 框选裁切 |
| POST | `/api/make-breathing-gif` | 呼吸动效 GIF |
| POST | `/api/gif-to-svga` | GIF → SVGA |

完整 API 与配置见根目录 `README.md`、`ENVIRONMENT.md`。
