# GPT 生图后端接入设计（新增可选后端）

## 1. 背景与目标

当前系统已支持多后端生图分发，核心入口为 `backend/app.py` 中的 `call_image_generator()`，通过 `IMAGE_BACKEND` 在 Lovart、ComfyUI、Stable Diffusion、Dreamina 之间切换。

本次目标是在不破坏现有交付链路的前提下，新增 GPT 生图能力：

- 接入方式：新增可选后端（不是替换默认后端）
- 能力范围：一步到位全量（文生图 + 图生图/编辑 + 局部编辑可接）
- API 优先级：OpenAI 官方接口优先
- 前端策略：支持 2-3 个模型档位（快 / 均衡 / 高质量）

## 2. 非目标

- 不重构现有所有后端为统一抽象基类
- 不在本版本引入“GPT 失败自动回退 Lovart”
- 不改变现有任务队列与历史记录的主流程协议

## 3. 方案选择

采用方案 A：新增独立 GPT 客户端并接入现有分发入口。

- 新增 `backend/gpt_image_client.py`，封装 OpenAI 生图调用
- 在 `backend/app.py` 内新增 `call_gpt(...)`
- 在 `call_image_generator()` 中增加 `backend == "gpt"` 分支
- 保持现有后端并存：`lovart / gpt / comfyui / stable_diffusion / dreamina`

选择原因：

- 变更范围小，回归风险低
- 与现有客户端模块形态一致，维护成本可控
- 后续扩展供应商时可复用同样接入方式

## 4. 架构与模块边界

### 4.1 新增模块

`backend/gpt_image_client.py` 职责：

- 接收标准化输入（prompt、尺寸、输入图、mask、模型名等）
- 发起 OpenAI 官方生图请求
- 将响应标准化为统一返回：成功返回图片 URL；失败返回可读错误
- 抛出模块内定义的错误类型，供 `app.py` 翻译前端文案

### 4.2 现有入口改动

`backend/app.py` 改动点：

- `normalize_image_backend()` 增加 `gpt` 识别
- 新增 `call_gpt(...)`，将内部参数映射到 GPT 客户端
- `call_image_generator()` 增加 `if backend == "gpt"` 分发
- 前端字段解析处增加 `gpt_tier`（默认 `balanced`）

### 4.3 前端改动

`backend/templates/index.html` 改动点：

- 生图后端下拉增加 `GPT`
- 新增 GPT 档位下拉（`fast / balanced / quality`）
- 仅当 `image_backend === "gpt"` 时显示档位选择
- 保持表单字段延续性，提交 `gpt_tier`

## 5. 参数映射设计

## 5.1 输入契约（沿用现有）

- `mode`: `text2img | img2img`
- `prompt`
- `image_paths`（可空）
- `ratio / output_width / output_height`
- `poll_timeout`
- `image_backend`
- `gpt_tier`（新增）

### 5.2 模式映射

- `text2img`：走 GPT 文生图能力
- `img2img` 且无 mask：走 GPT 图片编辑/变体能力（原图 + prompt）
- `img2img` 且有 mask：走 GPT 局部编辑/inpaint（原图 + mask + prompt）

输入不满足 API 要求时直接返回参数错误，不做静默降级。

### 5.3 尺寸映射

- 尺寸入口继续使用 `resolve_output_dimensions()`
- 将解析后的宽高映射为 GPT 请求参数
- 若尺寸不在 GPT 支持范围：
  - 优先映射到兼容尺寸并返回 warning（日志记录）
  - 仅在无法映射时返回明确错误

### 5.4 模型档位映射

- `fast -> OPENAI_IMAGE_MODEL_FAST`
- `balanced -> OPENAI_IMAGE_MODEL_BALANCED`
- `quality -> OPENAI_IMAGE_MODEL_QUALITY`

后端兜底顺序：

1. 用户档位对应模型
2. `OPENAI_IMAGE_MODEL_BALANCED`
3. 默认模型（固定安全值）

## 6. 环境变量设计

新增以下配置：

- `OPENAI_API_KEY`（必填）
- `OPENAI_BASE_URL`（可选，默认官方地址）
- `OPENAI_IMAGE_MODEL_FAST`
- `OPENAI_IMAGE_MODEL_BALANCED`
- `OPENAI_IMAGE_MODEL_QUALITY`
- `OPENAI_IMAGE_TIMEOUT`（可选，默认复用现有超时策略）

`IMAGE_BACKEND` 增加可选值 `gpt`。

## 7. 稳定性与错误处理

### 7.1 错误翻译

- `401/403`：API Key 无效或权限不足
- `429`：请求过多或额度受限
- `5xx/网络超时`：服务暂时不可用
- 参数错误：输入不符合 GPT 生图要求

后端日志保留原始错误细节，前端返回友好文案。

### 7.2 重试策略

仅对可重试错误触发重试：`429 / 5xx / timeout`。

- 重试次数：最多 3 次
- 退避策略：`1s -> 2s -> 4s`
- 参数错误不重试

### 7.3 并发与超时

- 复用现有任务流，不新增 GPT 专用队列
- 单请求设置 timeout，默认与现有生成超时口径保持一致

## 8. 返回协议兼容

保持现有统一返回约定：

- 成功：`(image_url, None)`
- 失败：`(None, error_message)`

这样 `generate_variants()`、任务轮询、下载命名链路均无需协议改造。

## 9. 验收与测试清单

### 9.1 基础通路

- `IMAGE_BACKEND=gpt` 时可正常出图
- `IMAGE_BACKEND=lovart` 等原后端行为不变

### 9.2 能力覆盖

- 文生图成功（3 种档位各至少 1 次）
- 图生图成功（带参考图）
- 局部编辑成功（带 mask）

### 9.3 异常场景

- 无 API Key：返回清晰配置错误
- 错误 Key：401/403 文案正确
- 触发限流：429 重试后返回合理错误
- 超时场景：超时后退出且不阻塞后续任务

### 9.4 兼容性

- 多图变体生成流程可用
- 历史记录可正常展示结果
- 前端切换后端与档位后可持久化（沿用现有 localStorage 习惯）

## 10. 实施顺序

1. 新增 `gpt_image_client.py`（含错误类型、请求封装、响应解析）
2. 修改 `app.py`：后端识别、分发、`call_gpt(...)`、字段读取
3. 修改 `index.html`：后端选项与 GPT 档位 UI
4. 更新 `.env.example`、`README.md` 的配置说明
5. 本地回归测试并补充必要日志

## 11. 风险与缓解

- OpenAI 生图尺寸约束与当前尺寸需求不一致  
  缓解：先做可映射策略，无法映射时明确报错并提示可用尺寸。

- 成本不可控（高质量档位频繁使用）  
  缓解：默认档位设为 balanced，必要时在后端增加项目级限流或白名单。

- 上线初期错误定位困难  
  缓解：按后端/档位/尺寸/耗时/错误码输出结构化日志。
