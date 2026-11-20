"""增量采集业务规则：状态流转、字段校验与筛选口径都收在这里。

游标只回答“从哪里开始采”，可续做的增量采集批次记录“这一轮采到了什么”：

- 批次登记本轮已交付的记录标识，以及还需补采的数据（原日志序号 + 记录标识 + 原盘点组）；
- 同一日志序号下的记录允许部分成功，确认位置推进后，未确认记录仍留在补采清单里可查；
- 已交付的记录在重跑与下一轮中都不会再次交付。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "incremental"
CHANGE_TABLE = "incremental_change_log"
BATCH_TABLE = "incremental_batch"
REQUIRED_FIELDS = ["游标编号", "来源标识", "增量字段"]
STATUS_ORDER = ["待启动", "运行中", "已完成", "失败"]
ACTION_RULES = {"启动采集": "运行中", "暂停采集": "待启动", "标记失败": "失败"}
NEGATIVE_ACTIONS = []

BATCH_OPEN = "采集中"
BATCH_PARTIAL = "待补采"
BATCH_DONE = "已完成"
RECOLLECT_PENDING = "待补采"
RECOLLECT_DONE = "已补采"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _record_key(seq: Any, record_id: Any) -> tuple[int, str]:
    """记录位置 = 日志序号 + 记录标识，全链路都用它做去重与核对。"""
    return _as_int(seq), str(record_id or "").strip()


class IncrementalService:
    # ---------- 游标登记与状态流转 ----------
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
            rows = [row for row in rows if keyword in str(row.get("游标编号", ""))]
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
            return None, f"增量游标 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于增量采集可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"增量游标已{action}"

    # ---------- 来源变更记录 ----------
    def _change_rows(self, source: str | None = None) -> list[dict[str, Any]]:
        rows = store.extra_rows(CHANGE_TABLE)
        if source is not None:
            rows = [row for row in rows if row.get("来源标识") == source]
        return sorted(rows, key=lambda row: (_as_int(row.get("日志序号")), _as_int(row.get("id"))))

    def _find_change(self, source: str, seq: int, record_id: str) -> dict[str, Any] | None:
        for row in self._change_rows(source):
            if _record_key(row.get("日志序号"), row.get("记录标识")) == (seq, record_id):
                return row
        return None

    def register_change(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记一条来源变更记录；同一日志序号下同一记录标识只允许登记一次。"""
        required = ["来源标识", "日志序号", "记录标识", "盘点组"]
        missing = [field for field in required if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        source = str(values.get("来源标识")).strip()
        seq = _as_int(values.get("日志序号"))
        record_id = str(values.get("记录标识")).strip()
        if self._find_change(source, seq, record_id) is not None:
            return None, f"来源 {source} 在日志序号 {seq} 下已存在记录 {record_id}，不重复登记"
        rows = store.extra_rows(CHANGE_TABLE)
        row = {
            "id": max((_as_int(item.get("id")) for item in rows), default=0) + 1,
            "来源标识": source,
            "日志序号": seq,
            "记录标识": record_id,
            "盘点组": str(values.get("盘点组")).strip(),
            "变更内容": str(values.get("变更内容") or ""),
            "记录时间": str(values.get("记录时间") or _now()),
            "可读": bool(values.get("可读", True)),
        }
        rows.append(row)
        return row, f"来源变更 {record_id}（日志序号 {seq}）已登记"

    def repair_change(self, change_id: int) -> tuple[dict[str, Any] | None, str]:
        """来源恢复后把变更记录置回可读，下一轮补采才能重试成功。"""
        for row in store.extra_rows(CHANGE_TABLE):
            if _as_int(row.get("id")) == change_id:
                row["可读"] = True
                return row, f"来源变更 {row.get('记录标识')} 已恢复可读，补采可重试"
        return None, f"来源变更记录 {change_id} 不存在"

    def list_change_groups(
        self, entry_id: int, since: int | None = None
    ) -> tuple[list[dict[str, Any]] | None, str]:
        """读取来源变更记录并按日志序号分组；默认只返回确认位置之后的部分。"""
        cursor = store.find(MODULE, entry_id)
        if cursor is None:
            return None, f"增量游标 {entry_id} 不存在或已归档"
        source = str(cursor.get("来源标识") or "")
        confirmed = self._confirmed_keys(str(cursor.get("游标编号") or ""))
        floor = self._confirmed_position(cursor) if since is None else since
        groups: dict[int, dict[str, Any]] = {}
        for row in self._change_rows(source):
            seq = _as_int(row.get("日志序号"))
            if seq <= floor:
                continue
            group = groups.setdefault(seq, {"日志序号": seq, "记录数": 0, "记录": []})
            record = dict(row)
            record["已交付"] = _record_key(seq, row.get("记录标识")) in confirmed
            group["记录"].append(record)
            group["记录数"] += 1
        return [groups[seq] for seq in sorted(groups)], ""

    # ---------- 批次状态推导 ----------
    def _cursor_batches(self, cursor_code: str) -> list[dict[str, Any]]:
        rows = [row for row in store.extra_rows(BATCH_TABLE) if row.get("游标编号") == cursor_code]
        return sorted(rows, key=lambda row: _as_int(row.get("id")))

    def _confirmed_keys(self, cursor_code: str) -> set[tuple[int, str]]:
        """游标维度已交付的记录位置：跨批次去重，重跑与下一轮都不会再交付。"""
        keys: set[tuple[int, str]] = set()
        for batch in self._cursor_batches(cursor_code):
            for item in batch.get("已交付记录", []):
                keys.add(_record_key(item.get("日志序号"), item.get("记录标识")))
        return keys

    def _pending_recollects(self, cursor_code: str) -> list[dict[str, Any]]:
        """游标维度的未完成清单：各批次补采队列里仍是待补采的条目。"""
        items: list[dict[str, Any]] = []
        for batch in self._cursor_batches(cursor_code):
            for entry in batch.get("补采清单", []):
                if entry.get("状态") == RECOLLECT_PENDING:
                    items.append(entry)
        return items

    def _confirmed_position(self, cursor: dict[str, Any]) -> int:
        positions = [
            _as_int(batch.get("确认位置"))
            for batch in self._cursor_batches(str(cursor.get("游标编号") or ""))
        ]
        positions.append(_as_int(cursor.get("当前水位")))
        return max(positions)

    # ---------- 增量采集批次 ----------
    def list_batches(self, cursor_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.extra_rows(BATCH_TABLE)
        if cursor_code:
            rows = [row for row in rows if row.get("游标编号") == cursor_code]
        return sorted(rows, key=lambda row: _as_int(row.get("id")))

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        for batch in store.extra_rows(BATCH_TABLE):
            if _as_int(batch.get("id")) == batch_id:
                return batch
        return None

    def open_batch(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """开启下一轮批次：范围 = 上一轮遗留的待补采项 + 确认位置之后的新变更。"""
        cursor = store.find(MODULE, entry_id)
        if cursor is None:
            return None, f"增量游标 {entry_id} 不存在或已归档"
        cursor_code = str(cursor.get("游标编号") or "")
        source = str(cursor.get("来源标识") or "")
        batches = self._cursor_batches(cursor_code)
        if any(batch.get("状态") == BATCH_OPEN for batch in batches):
            return None, "上一轮批次还未推进确认位置，不能重复开批"
        start = self._confirmed_position(cursor)
        confirmed = self._confirmed_keys(cursor_code)
        scope: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        # 补采项保留原记录位置（原日志序号）与原盘点组
        for item in self._pending_recollects(cursor_code):
            key = _record_key(item.get("日志序号"), item.get("记录标识"))
            if key in confirmed or key in seen:
                continue
            change = self._find_change(source, *key)
            scope.append({
                "日志序号": key[0],
                "记录标识": key[1],
                "盘点组": item.get("盘点组"),
                "变更内容": (change or {}).get("变更内容", ""),
                "可读": bool((change or {}).get("可读", True)),
                "任务类型": "补采",
            })
            seen.add(key)
        for row in self._change_rows(source):
            seq = _as_int(row.get("日志序号"))
            if seq <= start:
                continue
            key = _record_key(seq, row.get("记录标识"))
            if key in confirmed or key in seen:
                continue
            scope.append({
                "日志序号": seq,
                "记录标识": str(row.get("记录标识")),
                "盘点组": row.get("盘点组"),
                "变更内容": row.get("变更内容", ""),
                "可读": bool(row.get("可读", True)),
                "任务类型": "新采",
            })
            seen.add(key)
        if not scope:
            return None, "确认位置之后没有新的变更，也没有待补采记录，暂不需要开批"
        table = store.extra_rows(BATCH_TABLE)
        recollect_count = sum(1 for item in scope if item["任务类型"] == "补采")
        batch = {
            "id": max((_as_int(item.get("id")) for item in table), default=0) + 1,
            "批次编号": f"{cursor_code}-B{len(batches) + 1:02d}",
            "游标编号": cursor_code,
            "来源标识": source,
            "轮次": len(batches) + 1,
            "起始位置": start,
            "确认位置": start,
            "范围记录": scope,
            "覆盖序号": sorted({item["日志序号"] for item in scope}),
            "已交付记录": [],
            "补采清单": [],
            "状态": BATCH_OPEN,
            "创建时间": _now(),
            "完成时间": "",
        }
        table.append(batch)
        cursor["status"] = "运行中"
        cursor["pending"] = True
        cursor["上次采集"] = _now()
        return batch, f"批次 {batch['批次编号']} 已开启，本轮范围 {len(scope)} 条（补采 {recollect_count} 条）"

    def _find_scope_record(
        self, batch: dict[str, Any], record_id: str, seq: int | None = None
    ) -> dict[str, Any] | None:
        for item in batch.get("范围记录", []):
            if str(item.get("记录标识")) != record_id:
                continue
            if seq is not None and _as_int(item.get("日志序号")) != seq:
                continue
            return item
        return None

    def _register_recollect(self, batch: dict[str, Any], record: dict[str, Any], reason: str) -> None:
        """登记补采：保留原日志序号与原盘点组；同一记录位置在队列里只占一格。"""
        key = _record_key(record.get("日志序号"), record.get("记录标识"))
        queue = batch.setdefault("补采清单", [])
        for entry in queue:
            if entry.get("状态") == RECOLLECT_PENDING and _record_key(
                entry.get("日志序号"), entry.get("记录标识")
            ) == key:
                return
        queue.append({
            "日志序号": key[0],
            "记录标识": key[1],
            "盘点组": record.get("盘点组"),
            "原因": reason,
            "状态": RECOLLECT_PENDING,
            "登记批次": batch.get("批次编号"),
            "登记时间": _now(),
            "补采完成时间": "",
        })

    def _resolve_recollects(self, cursor_code: str, key: tuple[int, str]) -> None:
        """记录交付成功后，把各批次补采队列里同一记录位置的待办销掉（条目保留，状态翻转）。"""
        for batch in self._cursor_batches(cursor_code):
            for entry in batch.get("补采清单", []):
                if entry.get("状态") == RECOLLECT_PENDING and _record_key(
                    entry.get("日志序号"), entry.get("记录标识")
                ) == key:
                    entry["状态"] = RECOLLECT_DONE
                    entry["补采完成时间"] = _now()

    def confirm_record(
        self, batch_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """确认一条记录交付成功；重复确认直接跳过，不重复交付。"""
        batch = self.get_batch(batch_id)
        if batch is None:
            return None, f"增量采集批次 {batch_id} 不存在", False
        if batch.get("状态") != BATCH_OPEN:
            return batch, f"批次 {batch.get('批次编号')} 已推进确认位置，未确认记录请走下一轮补采", False
        record_id = str(values.get("记录标识") or "").strip()
        if not record_id:
            return batch, "缺少必填字段：记录标识", False
        seq = _as_int(values["日志序号"]) if values.get("日志序号") not in (None, "") else None
        record = self._find_scope_record(batch, record_id, seq)
        if record is None:
            return batch, f"记录 {record_id} 不在本批次范围内", False
        key = _record_key(record.get("日志序号"), record_id)
        delivered = {
            _record_key(item.get("日志序号"), item.get("记录标识"))
            for item in batch.get("已交付记录", [])
        }
        if key in delivered:
            return batch, f"记录 {record_id} 本轮已交付，重跑跳过，不重复交付", True
        change = self._find_change(str(batch.get("来源标识") or ""), *key)
        if not bool((change or record).get("可读", True)):
            self._register_recollect(batch, record, "来源读取失败")
            return batch, f"记录 {record_id} 来源读取失败，已按原日志序号 {key[0]} 登记补采", False
        batch.setdefault("已交付记录", []).append({
            "日志序号": key[0],
            "记录标识": key[1],
            "盘点组": record.get("盘点组"),
            "交付时间": _now(),
        })
        self._resolve_recollects(str(batch.get("游标编号") or ""), key)
        return batch, f"记录 {record_id}（日志序号 {key[0]}）已确认交付", True

    def fail_record(
        self, batch_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """消费侧主动标记一条记录本轮失败，进入补采清单。"""
        batch = self.get_batch(batch_id)
        if batch is None:
            return None, f"增量采集批次 {batch_id} 不存在", False
        if batch.get("状态") != BATCH_OPEN:
            return batch, f"批次 {batch.get('批次编号')} 已推进确认位置，未确认记录请走下一轮补采", False
        record_id = str(values.get("记录标识") or "").strip()
        if not record_id:
            return batch, "缺少必填字段：记录标识", False
        seq = _as_int(values["日志序号"]) if values.get("日志序号") not in (None, "") else None
        record = self._find_scope_record(batch, record_id, seq)
        if record is None:
            return batch, f"记录 {record_id} 不在本批次范围内", False
        key = _record_key(record.get("日志序号"), record_id)
        delivered = {
            _record_key(item.get("日志序号"), item.get("记录标识"))
            for item in batch.get("已交付记录", [])
        }
        if key in delivered:
            return batch, f"记录 {record_id} 已交付，不能再标记失败", False
        reason = str(values.get("原因") or "下游交付失败").strip()
        self._register_recollect(batch, record, reason)
        return batch, f"记录 {record_id} 已按原日志序号 {key[0]} 登记补采：{reason}", True

    def advance_batch(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        """推进确认位置：未确认的范围记录全部转入补采清单，确认位置不被它们卡住。"""
        batch = self.get_batch(batch_id)
        if batch is None:
            return None, f"增量采集批次 {batch_id} 不存在"
        if batch.get("状态") != BATCH_OPEN:
            return None, f"批次 {batch.get('批次编号')} 已推进过确认位置，不能重复推进"
        delivered = {
            _record_key(item.get("日志序号"), item.get("记录标识"))
            for item in batch.get("已交付记录", [])
        }
        for record in batch.get("范围记录", []):
            if _record_key(record.get("日志序号"), record.get("记录标识")) not in delivered:
                self._register_recollect(batch, record, "本轮未确认")
        covered = [_as_int(seq) for seq in batch.get("覆盖序号", [])]
        batch["确认位置"] = max(covered) if covered else _as_int(batch.get("起始位置"))
        pending = [entry for entry in batch.get("补采清单", []) if entry.get("状态") == RECOLLECT_PENDING]
        batch["状态"] = BATCH_PARTIAL if pending else BATCH_DONE
        batch["完成时间"] = _now()
        for cursor in store.rows(MODULE):
            if cursor.get("游标编号") == batch.get("游标编号"):
                cursor["当前水位"] = batch["确认位置"]
                cursor["上次采集"] = _now()
        return batch, f"批次 {batch['批次编号']} 确认位置已推进到 {batch['确认位置']}，待补采 {len(pending)} 条"

    def unconfirmed_records(
        self, batch_id: int, seq: int | None = None
    ) -> tuple[list[dict[str, Any]] | None, str]:
        """核对批次内未确认的记录：确认位置推进后，仍可按原日志序号找到每一条。"""
        batch = self.get_batch(batch_id)
        if batch is None:
            return None, f"增量采集批次 {batch_id} 不存在"
        delivered = {
            _record_key(item.get("日志序号"), item.get("记录标识"))
            for item in batch.get("已交付记录", [])
        }
        recollect_status = {
            _record_key(entry.get("日志序号"), entry.get("记录标识")): entry.get("状态")
            for entry in batch.get("补采清单", [])
        }
        items: list[dict[str, Any]] = []
        for record in batch.get("范围记录", []):
            key = _record_key(record.get("日志序号"), record.get("记录标识"))
            if key in delivered:
                continue
            if seq is not None and key[0] != seq:
                continue
            items.append({**record, "补采状态": recollect_status.get(key, "未登记")})
        return items, ""

    # ---------- 下一轮待采任务（供采集调度读取） ----------
    def next_round_tasks(self, cursor_code: str) -> tuple[dict[str, Any] | None, str]:
        """按批次确认位置与未完成清单推导下一轮待采任务；补采项关联原盘点组。"""
        cursor = None
        for row in store.rows(MODULE):
            if str(row.get("游标编号") or "") == cursor_code:
                cursor = row
                break
        if cursor is None:
            return None, f"增量游标 {cursor_code} 不存在或已归档"
        source = str(cursor.get("来源标识") or "")
        position = self._confirmed_position(cursor)
        confirmed = self._confirmed_keys(cursor_code)
        tasks: list[dict[str, Any]] = []
        pending_keys: set[tuple[int, str]] = set()
        for entry in self._pending_recollects(cursor_code):
            key = _record_key(entry.get("日志序号"), entry.get("记录标识"))
            pending_keys.add(key)
            tasks.append({
                "任务类型": "补采",
                "日志序号": key[0],
                "记录标识": key[1],
                "盘点组": entry.get("盘点组"),
                "登记批次": entry.get("登记批次"),
                "原因": entry.get("原因"),
            })
        for row in self._change_rows(source):
            seq = _as_int(row.get("日志序号"))
            if seq <= position:
                continue
            key = _record_key(seq, row.get("记录标识"))
            if key in confirmed or key in pending_keys:
                continue
            tasks.append({
                "任务类型": "新采",
                "日志序号": seq,
                "记录标识": str(row.get("记录标识")),
                "盘点组": row.get("盘点组"),
                "登记批次": "",
                "原因": "",
            })
        recollect_count = sum(1 for task in tasks if task["任务类型"] == "补采")
        plan = {
            "游标编号": cursor_code,
            "来源标识": source,
            "确认位置": position,
            "待采任务": tasks,
            "生成时间": _now(),
        }
        return plan, f"下一轮待采任务 {len(tasks)} 条（补采 {recollect_count} 条、新采 {len(tasks) - recollect_count} 条）"
