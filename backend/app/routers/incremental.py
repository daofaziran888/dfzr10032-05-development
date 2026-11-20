"""增量采集接口：维护增量游标与可续做的增量采集批次。

游标覆盖启动采集、暂停采集、标记失败等动作；批次负责逐轮采集：
读取来源变更分组、逐条确认交付、推进确认位置、留下补采清单。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.incremental import IncrementalService

router = APIRouter(prefix="/api/incremental", tags=["增量采集"])

service = IncrementalService()

LIST_FIELDS = ["游标编号", "来源标识", "增量字段", "当前水位", "批次大小", "采集间隔", "上次采集", "下次采集"]
STATUSES = ["待启动", "运行中", "已完成", "失败"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出增量采集清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "incremental", "total": total, "items": items}


# ---- 增量采集批次与来源变更记录（字面路径要先于 /{entry_id} 声明，避免被游标详情吃掉） ----


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    游标编号: str | None = Query(default=None, description="按游标编号过滤批次"),
) -> PageResult[dict]:
    """列出增量采集批次；每个批次都带着确认位置、已交付记录与补采清单。"""
    items = service.list_batches(游标编号)
    return PageResult(items=items, total=len(items))


@router.get("/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict:
    """读取单个批次明细：范围记录、本轮已交付的记录标识、补采清单。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"增量采集批次 {batch_id} 不存在")
    return batch


@router.get("/batches/{batch_id}/unconfirmed")
def batch_unconfirmed(
    batch_id: int,
    seq: int | None = Query(default=None, description="按日志序号过滤未确认记录"),
) -> dict[str, Any]:
    """核对批次内未确认的记录；确认位置推进后，仍可按原日志序号找到每一条。"""
    items, message = service.unconfirmed_records(batch_id, seq)
    if items is None:
        raise HTTPException(status_code=404, detail=message)
    return {"items": items, "total": len(items)}


@router.post("/batches/{batch_id}/confirm", response_model=ActionResult)
def confirm_record(batch_id: int, payload: EntryPayload) -> ActionResult:
    """确认一条记录交付成功；重复确认会被跳过，不重复交付。"""
    batch, message, ok = service.confirm_record(batch_id, payload.values)
    return ActionResult(ok=ok, message=message, entry=batch)


@router.post("/batches/{batch_id}/fail", response_model=ActionResult)
def fail_record(batch_id: int, payload: EntryPayload) -> ActionResult:
    """把一条记录标记为本轮失败，按原日志序号与原盘点组登记补采。"""
    batch, message, ok = service.fail_record(batch_id, payload.values)
    return ActionResult(ok=ok, message=message, entry=batch)


@router.post("/batches/{batch_id}/advance", response_model=ActionResult)
def advance_batch(batch_id: int) -> ActionResult:
    """推进批次确认位置；未确认的记录转入补采清单，不阻塞后续序号。"""
    batch, message = service.advance_batch(batch_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/changes", response_model=ActionResult)
def register_change(payload: EntryPayload) -> ActionResult:
    """登记一条来源变更记录；同一日志序号下同一记录标识不重复登记。"""
    row, message = service.register_change(payload.values)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.post("/changes/{change_id}/repair", response_model=ActionResult)
def repair_change(change_id: int) -> ActionResult:
    """来源恢复后把变更记录置回可读，补采重试才能成功。"""
    row, message = service.repair_change(change_id)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


# ---- 增量游标 ----


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按游标编号检索"),
    status: str | None = Query(default=None, description="待启动、运行中、已完成、失败"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按游标编号与状态过滤增量采集列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条增量游标明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"增量游标 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/changes")
def list_change_groups(
    entry_id: int,
    since: int | None = Query(default=None, description="只返回该日志序号之后的变更分组"),
) -> dict[str, Any]:
    """读取来源变更记录并按日志序号分组；默认从游标确认位置之后开始。"""
    groups, message = service.list_change_groups(entry_id, since)
    if groups is None:
        raise HTTPException(status_code=404, detail=message)
    return {"items": groups, "total": len(groups)}


@router.post("/{entry_id}/batches", response_model=ActionResult)
def open_batch(entry_id: int) -> ActionResult:
    """开启下一轮批次：范围 = 待补采项 + 确认位置之后的新变更。"""
    batch, message = service.open_batch(entry_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条增量游标，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="增量游标已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条增量游标执行启动采集、暂停采集、标记失败；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
