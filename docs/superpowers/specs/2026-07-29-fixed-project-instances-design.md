# 双实例锁定项目（FIXED_PROJECT）+ 移除门禁

**日期：** 2026-07-29  
**状态：** 已确认  
**范围：** 小灯塔 / 画啦啦分实例部署；页面标题区分；去掉项目组选择与门禁。

---

## 目标

| 实例 | 本机配置 | 远端目录 | 端口 | 固定项目 | 标题 |
|------|----------|----------|------|----------|------|
| 小灯塔 | `.env` | `/home/xiaoA` | `.env` 的 `PORT` | `FIXED_PROJECT=小灯塔` | `A-智绘 · 小灯塔` |
| 画啦啦 | `.env.hll` | `/home/xiaoA-hll` | `8629` | `FIXED_PROJECT=画啦啦` | `A-智绘 · 画啦啦` |

- Key 仍用现有 `_XDT` / `_HLL` 后缀（各 `.env` 只配本实例所需 Key）
- **删除门禁**：无密码弹层、无 Bearer token 校验、无 `/api/project-unlock`

## 行为

1. 后端读 `FIXED_PROJECT`，所有 API 默认该项目组
2. 前端隐藏项目组选择；标题与设计类型按固定项目加载
3. `./deploy.sh remote` 同步 `.env`；`./deploy.sh remote-hll` 用 `.env.hll` 覆盖远端 `.env`，并确保 `PORT=8629`、`FIXED_PROJECT=画啦啦`

## 非目标

- 不把 Key 改成无后缀全局变量
- 不合并两个远端目录
