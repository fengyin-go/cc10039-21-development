"""湖内质量监控领域服务：配置数据湖内数据的完整性、时效性和准确性监控规则。

上游接收入湖管道完成事件，对入湖数据执行质量检查。
下游联动血缘图谱记录质量问题，再汇总到治理报告中。

复核流程：治理报告导出"质量报告复核包"后，本模块读取复核包，
关联湖表、规则与来源报表，生成实际的复查记录，供值班员与导出数据对账。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.services.review_package import (
    PACKAGE_TYPE,
    ROUND_LABELS,
    package_digest,
    summarize_evidence,
)
from app.store import store


class QualityMonitorService(BaseModuleService):
    MODULE = "quality_monitor"
    ENTITY = "监控规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "监控指标", "告警阈值", "目标湖表", "检查频率"]
    REQUIRED_FIELDS = ["规则名称", "监控指标", "告警阈值"]
    STATUS_ORDER = ["待启用", "已启用", "已停用"]
    ACTION_RULES = {"启用监控": "已启用", "停用监控": "已停用"}
    NEGATIVE_ACTIONS = ["停用监控"]

    def enable_monitor(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """启用监控规则：验证目标湖表存在。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "监控规则不存在"
        if entry.get("status") != "待启用":
            return None, f"监控规则状态为{entry.get('status')}，不可启用"

        table_name = entry.get("目标湖表", "")

        # 验证目标湖表已注册
        lake_tables = store.find_by_field("lake_table", "表名", table_name)
        if not lake_tables:
            return None, f"目标湖表 {table_name} 不存在"

        entry["status"] = "已启用"
        entry["pending"] = False
        entry["abnormal"] = False

        store.log_event("quality_rule_enabled", "quality_monitor", "lake_table", {
            "rule_id": entry_id,
            "table_name": table_name,
            "metric": entry.get("监控指标", ""),
        })

        return entry, f"监控规则 {entry.get('规则名称')} 已启用，指标={entry.get('监控指标')}"

    def check_quality(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """执行质量检查：对目标湖表数据执行指标检查，命中阈值则触发告警。
        每笔检查落一条检查记录，作为治理报告质量指标的证据明细。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "监控规则不存在"
        if entry.get("status") != "已启用":
            return None, f"监控规则状态为{entry.get('status')}，不可执行检查"

        table_name = entry.get("目标湖表", "")
        metric = entry.get("监控指标", "")
        threshold = entry.get("告警阈值", "")

        # 模拟检查：统计目标湖表的数据行数
        lake_table_rows = store.find_by_field("lake_table", "表名", table_name)
        row_count = len(lake_table_rows)

        # 阈值比较
        try:
            threshold_value = int(threshold) if threshold.isdigit() else 0
        except (ValueError, AttributeError):
            threshold_value = 0

        passed = row_count >= threshold_value
        record = store.record_quality_check({
            "rule_id": entry_id,
            "rule_name": entry.get("规则名称"),
            "table_name": table_name,
            "metric": metric,
            "threshold": threshold,
            "actual_value": row_count,
            "passed": passed,
            "checked_at": datetime.datetime.now().isoformat(),
        })

        # 质量不通过时联动血缘和治理报告
        if not passed:
            store.log_event("quality_failed_to_lineage", "quality_monitor", "lineage_graph", {
                "rule_id": entry_id,
                "check_id": record["check_id"],
                "table_name": table_name,
                "metric": metric,
                "threshold": threshold,
                "actual": row_count,
            })

            # 更新血缘链路为待验证
            lineages = store.find_by_field("lineage_graph", "中间转换", table_name)
            if not lineages:
                lineages = store.find_by_field("lineage_graph", "下游消费", table_name)
            for lin in lineages:
                lin["status"] = "待验证"
                lin["abnormal"] = True
        else:
            store.log_event("quality_passed_to_govern", "quality_monitor", "govern_report", {
                "rule_id": entry_id,
                "check_id": record["check_id"],
                "table_name": table_name,
                "metric": metric,
                "actual": row_count,
            })

        return entry, (
            f"质量检查完成：{metric}={row_count}，阈值={threshold}，"
            + ("通过" if passed else "不通过")
        )

    def review_package(self, package: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """读取质量报告复核包，生成一轮复查记录。

        规则来源以包内快照为准（与导出摘要、证据明细同一版本），
        湖表与来源报表按当前湖内数据关联；值班员可用返回的汇总
        把复查记录数与导出文件里的证据明细重新对一遍。"""
        if not isinstance(package, dict) or package.get("package_type") != PACKAGE_TYPE:
            return None, "文件不是有效的质量报告复核包"

        evidence = package.get("evidence")
        summary = package.get("summary")
        rule_source = package.get("rule_source")
        report_id = package.get("source_report", {}).get("report_id")
        if not isinstance(evidence, list) or not isinstance(summary, dict) \
                or not isinstance(rule_source, list) or report_id is None:
            return None, "复核包缺少摘要、证据明细或规则来源，无法复查"

        # 1) 核对包完整性：按包内明细重算内容摘要，与包自携摘要比对
        recomputed_digest = package_digest(package)
        digest_verified = recomputed_digest == package.get("digest")

        # 2) 核对复查范围：与上一次复查读到的包摘要比对，范围变化要提示
        previous_runs = store.review_runs()
        previous_digest = previous_runs[-1]["package_digest"] if previous_runs else None
        scope_changed = previous_digest is not None and previous_digest != recomputed_digest

        # 3) 关联来源报表（治理报告当前状态）
        source_report = store.find("govern_report", int(report_id))

        # 4) 逐条证据生成复查记录：关联表、规则（包内快照）和来源报表
        rules_by_id = {int(r.get("rule_id", 0)): r for r in rule_source}
        records: list[dict[str, Any]] = []
        for item in evidence:
            rule = rules_by_id.get(int(item.get("rule_id", 0)))
            table_name = item.get("table_name", "")
            lake_tables = store.find_by_field("lake_table", "表名", table_name)
            records.append({
                "package_version": package.get("package_version"),
                "package_digest": package.get("digest"),
                "report_id": report_id,
                "report_status": source_report.get("status") if source_report else "来源报表缺失",
                "check_id": item.get("check_id"),
                "check_round": item.get("round"),
                "round_label": ROUND_LABELS.get(item.get("round"), "补查轮"),
                "rule_id": item.get("rule_id"),
                "rule_name": (rule or {}).get("规则名称", item.get("rule_name", "")),
                "rule_metric": (rule or {}).get("监控指标", item.get("metric", "")),
                "rule_threshold": (rule or {}).get("告警阈值", item.get("threshold", "")),
                "table_name": table_name,
                "lake_table_status": lake_tables[0].get("status") if lake_tables else "湖表缺失",
                "metric": item.get("metric"),
                "threshold": item.get("threshold"),
                "actual_value": item.get("actual_value"),
                "passed": item.get("passed"),
                "checked_at": item.get("checked_at"),
                "reviewed_at": datetime.datetime.now().isoformat(),
            })

        # 5) 按文件中的检查明细复算失败条数，与包内摘要核对
        recomputed = summarize_evidence(evidence)
        counts_match = (
            recomputed["quality_checks"] == summary.get("quality_checks")
            and recomputed["failures"] == summary.get("failures")
            and recomputed["passes"] == summary.get("passes")
        )

        run = store.record_review_run({
            "package_version": package.get("package_version"),
            "package_digest": package.get("digest"),
            "digest_verified": digest_verified,
            "scope_changed": scope_changed,
            "report_id": report_id,
            "evidence_count": len(evidence),
            "review_record_count": len(records),
            "recomputed_failures": recomputed["failures"],
            "summary_failures": summary.get("failures"),
            "counts_match": counts_match,
            "rounds": recomputed["rounds"],
            "reviewed_at": datetime.datetime.now().isoformat(),
        })
        for record in records:
            record["review_run_id"] = run["review_run_id"]
        saved = store.add_review_records(records)

        store.log_event("quality_review_run_recorded", "quality_monitor", "govern_report", {
            "review_run_id": run["review_run_id"],
            "report_id": report_id,
            "package_version": package.get("package_version"),
            "package_digest": package.get("digest"),
            "review_record_count": len(saved),
            "recomputed_failures": recomputed["failures"],
        })

        late_rounds = [r for r in recomputed["rounds"] if r != 1]
        late_note = (
            f"，迟到检查安排在{ROUND_LABELS.get(max(late_rounds), '补查轮')}"
            if late_rounds else ""
        )
        return run, (
            f"复查完成：生成复查记录={len(saved)} 条，复算失败={recomputed['failures']} 条"
            f"（包内摘要={summary.get('failures')} 条）{late_note}"
            + ("" if digest_verified else "；警告：包内容摘要校验不一致")
            + ("；注意：复查范围较上一轮已变化" if scope_changed else "")
        )

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "启用监控":
            return self.enable_monitor(entry_id)
        if action == "执行检查":
            return self.check_quality(entry_id)
        return super().run_action(entry_id, action)
