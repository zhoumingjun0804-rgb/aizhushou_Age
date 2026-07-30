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

同一套代码与 Key；不新建本机 `.env.hll`。两个远端实例分别锁定项目组：

- 小灯塔实例固定 `FIXED_PROJECT=小灯塔`
- 画啦啦实例固定 `FIXED_PROJECT=画啦啦`
- 页面隐藏项目组切换栏，自动加载当前实例的项目素材
- 浏览器标题与页面标题显示当前固定项目组
- 后端只返回当前项目组的数据，并拒绝跨项目请求

---

## 命令

```bash
./deploy.sh remote            # 小灯塔：同步 + 写 FIXED_PROJECT=小灯塔 + 远端 deploy
./deploy.sh remote sync       # 仅同步小灯塔实例配置，不重启
./deploy.sh remote-hll        # 画啦啦：同步 + 写 FIXED_PROJECT=画啦啦、PORT=8629 + 远端 deploy
./deploy.sh remote-hll sync   # 仅同步画啦啦实例配置，不重启
```

## 流程

1. 读取本机 `.env` 的 `TEST_*`（与 `remote` 相同）
2. rsync 到目标实例目录（排除规则同现有 remote）
3. SSH 修改目标目录的远端 `.env`（不改本机）：
   - `remote` 写入 `FIXED_PROJECT=小灯塔`
   - `remote-hll` 写入 `FIXED_PROJECT=画啦啦` 和 `PORT=8629`
4. 全量部署时使用对应 PM2 名执行远端 `deploy.sh`
5. 前端根据 `FIXED_PROJECT` 隐藏项目组切换栏，设置页面标题并自动加载对应项目
6. 后端根据 `FIXED_PROJECT` 过滤项目列表、历史记录等项目数据，并强制所有项目请求使用固定项目
7. 提示对应实例访问地址

## 验收标准

1. `./deploy.sh remote` 部署后仅显示小灯塔内容，标题包含“小灯塔”
2. `./deploy.sh remote-hll` 部署后仅显示画啦啦内容，标题包含“画啦啦”
3. 两个实例都不显示项目组切换栏
4. 画啦啦实例不会显示或通过 API 返回小灯塔素材、历史记录
5. 小灯塔实例不会显示或通过 API 返回画啦啦素材、历史记录
6. 直接提交另一项目组参数时，后端返回 `403`，不执行跨项目操作
7. 本机 `.env` 不被修改，部署流程不依赖 `.env.hll`

## 非目标

- 不拆分代码为两个分支或两套模板
- 不改变项目素材目录结构
- 不拆分本机两套 `.env`
