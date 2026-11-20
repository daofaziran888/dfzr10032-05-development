"""采集调度业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.incremental import IncrementalService
from app.store import store

MODULE = "schedule"
REQUIRED_FIELDS = ["计划编号", "关联任务", "调度周期"]
STATUS_ORDER = ["草稿", "已排期", "运行中", "已完成", "已暂停"]
ACTION_RULES = {"确认排期": "已排期", "启动调度": "运行中", "暂停调度": "已暂停"}
NEGATIVE_ACTIONS = []

incremental_service = IncrementalService()


class ScheduleService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("计划编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"调度计划 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集调度可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"调度计划已{action}"

    def plan_next_round(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
        """读取关联增量游标的批次确认位置与未完成清单，生成下一轮待采任务。

        补采项保留原日志序号并关联到原盘点组；已交付的记录不会进入下一轮。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, [], f"调度计划 {entry_id} 不存在或已归档"
        cursor_code = str(
            values.get("游标编号") or entry.get("关联游标") or entry.get("关联任务") or ""
        ).strip()
        if not cursor_code:
            return None, [], "调度计划未关联增量游标，无法生成下一轮待采任务"
        plan, message = incremental_service.next_round_tasks(cursor_code)
        if plan is None:
            return None, [], message
        entry["关联游标"] = cursor_code
        entry["确认位置"] = plan["确认位置"]
        entry["下一轮任务清单"] = plan["待采任务"]
        entry["任务生成时间"] = plan["生成时间"]
        return entry, plan["待采任务"], message
