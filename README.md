# 多源数据采集与RPA自动化平台

面向网页抓取、API接入、增量采集、RPA流程编排、数据校验与采集监控的数据工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据源配置 | `datasource` | 数据源 | 源端编号、连接类型、接入地址 |
| 网页采集规则 | `crawler` | 采集规则 | 规则编号、目标网址、页面选择器 |
| API接入管理 | `apiclient` | API客户端 | 客户端编号、接口名称、请求方法 |
| 增量采集 | `incremental` | 增量游标、增量采集批次 | 游标编号、来源标识、增量字段 |
| RPA任务编排 | `robot_task` | RPA任务 | 任务编号、任务名称、页面定位器 |
| 数据接入通道 | `ingestion` | 接入通道 | 通道编号、通道名称、输入格式 |
| 格式解析器 | `parser` | 解析配置 | 解析编号、输入格式、输出格式 |
| 采集结果校验 | `validator` | 校验规则 | 规则编号、校验字段、校验类型 |
| 采集去重 | `dedup` | 去重策略 | 策略编号、输入来源、去重键 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联任务、调度周期 |
| 采集监控 | `monitor` | 监控面板 | 面板编号、采集源总数、今日采集量 |
| 采集日志 | `log_viewer` | 日志条目 | 日志编号、关联任务、日志级别 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、凭证类型、关联数据源 |
| 采集转换管道 | `transform` | 转换节点 | 节点编号、输入来源、输出目标 |
| 数据流转编排 | `flow` | 流转管道 | 管道编号、管道名称、起始节点 |
| 数据血缘追踪 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 采集报告 | `report` | 采集报告 | 报告编号、采集源、统计周期 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 增量采集批次

游标只登记“从哪里开始采”，可续做的批次记录“这一轮采到了什么”：

- `GET /api/incremental/{游标id}/changes`：读取来源变更记录并按日志序号分组，
  默认只返回确认位置之后的部分；同一日志序号下的多条记录（如同一次盘点的多个货位）归为一组。
- `POST /api/incremental/{游标id}/batches`：开启下一轮批次，范围 = 上一轮遗留的待补采项
  （保留原日志序号与原盘点组）+ 确认位置之后的新变更；已交付的记录不会重复进入范围。
- `POST /api/incremental/batches/{批次id}/confirm`：确认一条记录交付成功，
  重复确认直接跳过，重跑不会重复交付；来源读失败时自动按原记录位置登记补采。
- `POST /api/incremental/batches/{批次id}/advance`：推进确认位置，
  未确认的记录全部转入补采清单，确认位置不被单条失败卡住。
- `GET /api/incremental/batches/{批次id}/unconfirmed?seq=6402`：确认位置推进后，
  仍可按原日志序号核对每一条未确认记录。
- `POST /api/schedule/{计划id}/next-round`：采集调度读取批次的确认位置与未完成清单，
  生成下一轮待采任务，补采项关联到原盘点组。

种子数据内置了验证场景：仓库库存来源 `WH-STOCK` 在序号 6402 返回同一盘点组
`PD-202611-01` 的三个货位，第二个货位 `BIN-A02` 读失败，随后的 6403 已经可读。
场景回归直接运行：

```bash
cd backend && python3 tests/test_incremental_batch.py
```
