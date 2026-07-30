# 生图队列状态刷新间隔调整设计

## 目标

将前端对 `/api/generation/queue-status` 的轮询间隔从 3 秒调整为 1 分钟，降低不必要的接口请求频率。

## 设计

- 将 `backend/templates/index.html` 中的 `QUEUE_STATUS_POLL_MS` 从 `3000` 改为 `60000`。
- 保留页面启动时立即调用一次 `refreshQueueStatus()` 的现有行为。
- 不修改接口、队列状态展示或鉴权逻辑。

## 验证

- 静态检查轮询常量为 `60000`。
- 运行相关测试，确认现有队列状态功能没有回归。
