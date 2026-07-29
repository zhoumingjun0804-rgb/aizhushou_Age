# 画啦啦远程部署（deploy.sh remote-hll）设计规格

**日期：** 2026-07-29  
**状态：** 已确认  
**范围：** 在本机增加 `./deploy.sh remote-hll`，与现有小灯塔 `./deploy.sh remote` 并存。

---

## 目标

测试机上同时跑两个实例：

| 实例 | 命令 | 远端目录 | PM2 名 | 端口 |
|------|------|----------|--------|------|
| 小灯塔 | `./deploy.sh remote` | `/home/xiaoA` | `aizhushou-age` | `.env` 中的 `PORT` |
| 画啦啦 | `./deploy.sh remote-hll` | `/home/xiaoA-hll` | `aizhushou-hll` | 固定 `8629` |

同一套代码与 Key；页面仍可选项目组。不新建本机 `.env.hll`。

---

## 命令

```bash
./deploy.sh remote-hll       # 同步到 /home/xiaoA-hll + 写 PORT=8629 + 远端 deploy
./deploy.sh remote-hll sync  # 仅 rsync + 写 PORT=8629，不重启
```

## 流程

1. 读取本机 `.env` 的 `TEST_*`（与 `remote` 相同）
2. rsync 到 `/home/xiaoA-hll/`（排除规则同现有 remote）
3. SSH 将远端 `.env` 的 `PORT` 设为 `8629`（不改本机）
4. 全量部署时：远端 `PM2_APP_NAME=aizhushou-hll ./deploy.sh`
5. 提示访问 `http://<服务器>:8629/`

## 非目标

- 不改变 `./deploy.sh remote` 行为
- 不锁定 UI 仅显示画啦啦
- 不拆分本机两套 `.env`
