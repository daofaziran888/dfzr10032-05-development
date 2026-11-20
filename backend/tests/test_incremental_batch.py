"""增量采集批次场景验证（只依赖标准库，直接运行）：

    python3 tests/test_incremental_batch.py

场景对应需求：仓库库存来源 WH-STOCK 在日志序号 6402 返回同次盘点（盘点组
PD-202611-01）的三个货位，第二个货位 BIN-A02 读失败，随后 6403 已经可读。
验证点：分组读取、本轮已交付记录、补采保留原记录位置、确认位置推进、
推进后未确认记录可查、重跑不重复交付、调度生成下一轮待采任务并关联原盘点组。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.incremental import IncrementalService
from app.services.schedule import ScheduleService
from app.store import store


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(f"验证失败：{message}")
    print(f"  ✓ {message}")


def main() -> None:
    store.reset()
    inc = IncrementalService()
    sch = ScheduleService()

    print("1. 读取来源变更记录并按日志序号分组")
    groups, _ = inc.list_change_groups(1)
    assert groups is not None
    check([g["日志序号"] for g in groups] == [6402, 6403], "水位 6401 之后按日志序号分组返回 6402、6403")
    first = groups[0]
    check(first["记录数"] == 3, "序号 6402 下挂着同次盘点的三个货位")
    check({r["盘点组"] for r in first["记录"]} == {"PD-202611-01"}, "三个货位属于同一盘点组 PD-202611-01")
    check([r["记录标识"] for r in first["记录"]] == ["BIN-A01", "BIN-A02", "BIN-A03"], "组内记录保持来源顺序")

    print("2. 开启第一轮批次，逐条确认（第二个货位读失败）")
    batch, msg = inc.open_batch(1)
    assert batch is not None, msg
    check(batch["起始位置"] == 6401 and batch["覆盖序号"] == [6402, 6403], "第一轮范围覆盖 6402、6403")
    check(batch["状态"] == "采集中", "批次状态为采集中")
    dup, _ = inc.open_batch(1)
    check(dup is None, "上一轮未推进确认位置时不能重复开批")
    b1 = int(batch["id"])

    _, _, ok = inc.confirm_record(b1, {"记录标识": "BIN-A01"})
    check(ok, "BIN-A01 确认交付")
    batch, msg, ok = inc.confirm_record(b1, {"记录标识": "BIN-A02"})
    assert batch is not None
    check(not ok and "来源读取失败" in msg, "BIN-A02 读失败，自动登记补采")
    entry = batch["补采清单"][0]
    check(entry["日志序号"] == 6402 and entry["盘点组"] == "PD-202611-01", "补采项保留原日志序号 6402 与原盘点组")
    batch, _, _ = inc.confirm_record(b1, {"记录标识": "BIN-A02"})
    assert batch is not None
    check(len(batch["补采清单"]) == 1, "同一条失败记录重复上报不重复登记补采")
    inc.confirm_record(b1, {"记录标识": "BIN-A03"})
    inc.confirm_record(b1, {"记录标识": "BIN-B01"})
    inc.confirm_record(b1, {"记录标识": "BIN-B02"})
    batch, msg, ok = inc.confirm_record(b1, {"记录标识": "BIN-A01"})
    assert batch is not None
    delivered = [d["记录标识"] for d in batch["已交付记录"]]
    check(ok and delivered.count("BIN-A01") == 1 and len(delivered) == 4, "重跑不重复交付成功项，本轮已交付 4 条")
    check("6402" in str(batch["已交付记录"]) and "6403" in str(batch["已交付记录"]), "已交付记录标识带原日志序号")

    print("3. 6402 未读全，随后的 6403 已经可读")
    later, _ = inc.list_change_groups(1, since=6402)
    assert later is not None
    check([g["日志序号"] for g in later] == [6403], "按 since=6402 读取时 6403 已经可读")

    print("4. 推进确认位置，未确认记录仍可核对")
    batch, msg = inc.advance_batch(b1)
    assert batch is not None
    check(batch["确认位置"] == 6403 and batch["状态"] == "待补采", "确认位置推进到 6403，批次状态待补采")
    cursor = store.find("incremental", 1)
    assert cursor is not None
    check(cursor["当前水位"] == 6403, "游标当前水位同步推进到 6403")
    items, _ = inc.unconfirmed_records(b1, 6402)
    assert items is not None
    check([i["记录标识"] for i in items] == ["BIN-A02"], "推进后仍找得到 6402 下的未确认记录 BIN-A02")
    check(items[0]["补采状态"] == "待补采", "该记录在补采队列中状态为待补采")
    items, _ = inc.unconfirmed_records(b1, 6403)
    check(items == [], "6403 下没有未确认记录")
    _, _, ok = inc.confirm_record(b1, {"记录标识": "BIN-A02"})
    check(not ok, "批次推进后不再接受本轮确认，补采走下一轮")

    print("5. 采集调度读取确认位置与未完成清单，生成下一轮待采任务")
    plan_entry, tasks, msg = sch.plan_next_round(1, {})
    assert plan_entry is not None
    check(len(tasks) == 1 and tasks[0]["任务类型"] == "补采", "下一轮只有 BIN-A02 一条补采任务")
    check(tasks[0]["日志序号"] == 6402 and tasks[0]["记录标识"] == "BIN-A02", "补采任务保留原记录位置 6402/BIN-A02")
    check(tasks[0]["盘点组"] == "PD-202611-01", "补采项关联到原盘点组 PD-202611-01")
    check(plan_entry["确认位置"] == 6403 and plan_entry["下一轮任务清单"] == tasks, "调度计划登记确认位置与待采清单")

    print("6. 来源恢复 + 新变更到达，第二轮批次续做")
    _, msg = inc.repair_change(2)
    check("已恢复可读" in msg, "BIN-A02 来源恢复可读")
    row, msg = inc.register_change({"来源标识": "WH-STOCK", "日志序号": 6404, "记录标识": "BIN-C01", "盘点组": "PD-202611-03"})
    check(row is not None, "登记序号 6404 的新变更 BIN-C01")
    _, tasks, _ = sch.plan_next_round(1, {})
    kinds = {(t["任务类型"], t["记录标识"]) for t in tasks}
    check(kinds == {("补采", "BIN-A02"), ("新采", "BIN-C01")}, "下一轮任务 = 补采 BIN-A02 + 新采 BIN-C01")

    batch2, msg = inc.open_batch(1)
    assert batch2 is not None, msg
    b2 = int(batch2["id"])
    scope = {(r["任务类型"], r["记录标识"], r["日志序号"]) for r in batch2["范围记录"]}
    check(scope == {("补采", "BIN-A02", 6402), ("新采", "BIN-C01", 6404)}, "第二轮只含补采项与新变更，已成功项不再出现")
    _, _, ok = inc.confirm_record(b2, {"记录标识": "BIN-A02"})
    check(ok, "来源恢复后 BIN-A02 补采成功")
    batch1 = inc.get_batch(b1)
    assert batch1 is not None
    hit = [e for e in batch1["补采清单"] if e["记录标识"] == "BIN-A02"][0]
    check(hit["状态"] == "已补采" and hit["日志序号"] == 6402, "原批次补采队列仍保留 BIN-A02 的原记录位置，状态转为已补采")
    inc.confirm_record(b2, {"记录标识": "BIN-C01"})
    batch2, msg = inc.advance_batch(b2)
    assert batch2 is not None
    check(batch2["确认位置"] == 6404 and batch2["状态"] == "已完成", "第二轮确认位置推进到 6404，批次完成")

    print("7. 全部交付后不再产生重复交付")
    plan, _ = inc.next_round_tasks("INCR-0001")
    assert plan is not None
    check(plan["待采任务"] == [], "下一轮待采任务为空")
    batch3, _ = inc.open_batch(1)
    check(batch3 is None, "没有新变更时不再开批，避免重跑重复交付")

    print("8. 同一轮内标记失败后，来源修复可立即重试确认")
    inc.register_change({"来源标识": "WH-STOCK", "日志序号": 6405, "记录标识": "BIN-D01", "盘点组": "PD-202611-04", "可读": False})
    batch4, _ = inc.open_batch(1)
    assert batch4 is not None
    b4 = int(batch4["id"])
    _, _, ok = inc.confirm_record(b4, {"记录标识": "BIN-D01"})
    check(not ok, "BIN-D01 来源读失败，登记补采")
    change_id = next(r["id"] for r in store.extra_rows("incremental_change_log") if r["记录标识"] == "BIN-D01")
    inc.repair_change(int(change_id))
    batch4, _, ok = inc.confirm_record(b4, {"记录标识": "BIN-D01"})
    assert batch4 is not None
    check(ok, "来源修复后同一轮内重试确认成功")
    hit = [e for e in batch4["补采清单"] if e["记录标识"] == "BIN-D01"][0]
    check(hit["状态"] == "已补采" and hit["日志序号"] == 6405, "同轮补采条目状态转为已补采，原记录位置保留")
    batch4, _ = inc.advance_batch(b4)
    assert batch4 is not None
    check(batch4["状态"] == "已完成" and batch4["确认位置"] == 6405, "第三轮无待补采，批次直接完成")

    print("\n全部场景验证通过")


if __name__ == "__main__":
    main()
