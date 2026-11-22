"""治理报告领域服务：定期生成湖内治理报告和自动优化建议。

治理报告是跨模块数据流的最终汇聚点：汇总冷热分层迁移、小文件合并、
保留策略清理、质量监控结果和血缘链路状态，生成综合治理报告。

质量复核：报告生成后可导出"质量报告复核包"，包内报告摘要、证据明细
和规则来源来自同一版本；报告生成后迟到的检查会作为补查轮次纳入，
导出中断重试时可凭内容摘要核对复查范围是否变化。
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


class GovernReportService(BaseModuleService):
    MODULE = "govern_report"
    ENTITY = "治理报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告周期", "治理指标", "优化建议", "执行状态", "审批状态"]
    REQUIRED_FIELDS = ["报告周期", "治理指标", "优化建议"]
    STATUS_ORDER = ["待生成", "已生成", "已审批", "已执行"]
    ACTION_RULES = {"生成报告": "已生成", "执行优化": "已执行"}
    NEGATIVE_ACTIONS = []

    def generate_report(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """生成治理报告：跨模块聚合数据生成综合治理报告。
        这是整个系统的最终数据产物消费入口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "治理报告不存在"
        if entry.get("status") != "待生成":
            return None, f"治理报告状态为{entry.get('status')}，不可生成"

        # 1) 汇总冷热分层迁移
        tier_events = store.events_for_module("storage_tier")
        tier_migrations = [e for e in tier_events if e["event_type"] == "tier_migration_executed"]
        total_migrated = sum(
            e["payload"].get("migrated_partition_count", 0) for e in tier_migrations
        )

        # 2) 汇总小文件合并
        compaction_events = store.events_for_module("file_compaction")
        completed_compactions = [e for e in compaction_events if e["event_type"] == "compaction_completed"]

        # 3) 汇总保留策略清理
        retention_events = store.events_for_module("retention_mgr")
        completed_cleanups = [e for e in retention_events if e["event_type"] == "retention_cleanup_executed"]

        # 4) 汇总质量监控结果：以实际检查记录为准，作为报告质量指标的证据
        quality_checks = store.quality_checks()
        quality_failures = [c for c in quality_checks if not c.get("passed")]
        quality_passes = [c for c in quality_checks if c.get("passed")]

        # 5) 汇总血缘链路状态
        lineage_rows = store.rows("lineage_graph")
        broken_lineages = [l for l in lineage_rows if l.get("status") == "链路断裂"]
        uncertain_lineages = [l for l in lineage_rows if l.get("status") == "待验证"]

        # 6) 汇总回填任务
        backfill_events = store.events_for_module("backfill_job")
        completed_backfills = [e for e in backfill_events if e["event_type"] == "backfill_completed_to_govern"]

        # 组装报告
        report_data = {
            "report_period": entry.get("报告周期", ""),
            "generated_at": datetime.datetime.now().isoformat(),
            "storage_governance": {
                "tier_migrations": len(tier_migrations),
                "total_partitions_migrated": total_migrated,
                "completed_compactions": len(completed_compactions),
                "completed_cleanups": len(completed_cleanups),
            },
            "quality_monitoring": {
                "quality_checks": len(quality_checks),
                "failures": len(quality_failures),
                "passes": len(quality_passes),
            },
            "lineage_health": {
                "total_lineages": len(lineage_rows),
                "broken": len(broken_lineages),
                "uncertain": len(uncertain_lineages),
                "healthy": len(lineage_rows) - len(broken_lineages) - len(uncertain_lineages),
            },
            "backfill_summary": {
                "completed_backfills": len(completed_backfills),
            },
        }

        # 计算优化建议
        suggestions = []
        if len(broken_lineages) > 0:
            suggestions.append(f"修复 {len(broken_lineages)} 条断裂血缘链路")
        if len(quality_failures) > 0:
            suggestions.append(f"处理 {len(quality_failures)} 个质量不达标指标")
        if total_migrated > 0:
            suggestions.append(f"冷热迁移有效，已迁移 {total_migrated} 个分区")
        if len(completed_compactions) > 0:
            suggestions.append(f"小文件合并完成 {len(completed_compactions)} 个任务")

        entry["status"] = "已生成"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["优化建议"] = "；".join(suggestions) if suggestions else "当前治理状态良好"
        entry["执行状态"] = "报告已生成"
        entry["治理指标"] = (
            f"质量检查{len(quality_checks)}次/失败{len(quality_failures)}次；"
            f"迁移分区{total_migrated}个；断裂血缘{len(broken_lineages)}条"
        )

        # 固化报告版本与证据截止点：之后的迟到检查归入补查轮次
        entry["_report_version"] = f"govrep-{entry_id}-v1"
        entry["_generated_at"] = report_data["generated_at"]
        entry["_evidence_cutoff"] = quality_checks[-1]["check_id"] if quality_checks else 0
        entry["_report_data"] = report_data

        store.log_event("govern_report_generated", "govern_report", "govern_report", {
            "report_id": entry_id,
            "report_version": entry["_report_version"],
            "evidence_cutoff": entry["_evidence_cutoff"],
            "report_data": report_data,
        })

        return entry, (
            f"治理报告已生成：迁移分区={total_migrated}，质量检查={len(quality_checks)}，"
            f"血缘链路={len(lineage_rows)}，回填任务={len(completed_backfills)}"
        )

    def build_review_package(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """组装质量报告复核包：报告摘要、证据明细、规则来源同一版本。

        每次导出（含中断重试）都会记录一条导出尝试，凭包版本与内容摘要
        可核对复查范围是否变化。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "治理报告不存在"
        if entry.get("status") == "待生成":
            return None, "治理报告尚未生成，无可复核的质量指标"

        # 历史报告（无版本信息）首次导出时补齐版本与证据截止点，
        # 之后每次导出沿用同一版本，保证重试可比对。
        if not entry.get("_report_version"):
            entry["_report_version"] = f"govrep-{entry_id}-v1"
        if entry.get("_evidence_cutoff") is None:
            checks_now = store.quality_checks()
            entry["_evidence_cutoff"] = checks_now[-1]["check_id"] if checks_now else 0

        cutoff = int(entry["_evidence_cutoff"])

        # 证据明细：全部检查记录按轮次标注；截止点之后到的为迟到补查
        evidence = []
        for check in store.quality_checks():
            round_no = 1 if int(check["check_id"]) <= cutoff else 2
            evidence.append({
                "check_id": check["check_id"],
                "round": round_no,
                "round_label": ROUND_LABELS[round_no],
                "rule_id": check.get("rule_id"),
                "rule_name": check.get("rule_name"),
                "table_name": check.get("table_name"),
                "metric": check.get("metric"),
                "threshold": check.get("threshold"),
                "actual_value": check.get("actual_value"),
                "passed": check.get("passed"),
                "checked_at": check.get("checked_at"),
            })

        # 报告摘要按包内证据明细复算，保证摘要与明细是同一版本
        summary = summarize_evidence(evidence)
        summary["rounds"] = {str(k): v for k, v in summary["rounds"].items()}

        # 规则来源：证据涉及的监控规则快照，与摘要、明细同一次读取
        rule_ids = {int(item["rule_id"]) for item in evidence if item.get("rule_id") is not None}
        rule_source = []
        for rule in store.rows("quality_monitor"):
            if int(rule.get("id", 0)) in rule_ids:
                rule_source.append({
                    "rule_id": rule.get("id"),
                    "规则名称": rule.get("规则名称", ""),
                    "监控指标": rule.get("监控指标", ""),
                    "告警阈值": rule.get("告警阈值", ""),
                    "目标湖表": rule.get("目标湖表", ""),
                    "检查频率": rule.get("检查频率", ""),
                    "status": rule.get("status", ""),
                })

        target_tables = sorted({item["table_name"] for item in evidence if item.get("table_name")})

        package: dict[str, Any] = {
            "package_type": PACKAGE_TYPE,
            "package_version": entry["_report_version"],
            "exported_at": datetime.datetime.now().isoformat(),
            "source_report": {
                "report_id": entry_id,
                "报告周期": entry.get("报告周期", ""),
                "status": entry.get("status", ""),
                "generated_at": entry.get("_generated_at", ""),
                "治理指标": entry.get("治理指标", ""),
            },
            "summary": summary,
            "evidence": evidence,
            "rule_source": rule_source,
            "target_tables": target_tables,
        }
        package["digest"] = package_digest(package)

        # 记录导出尝试：中断重试后凭版本与摘要核对复查范围是否改变
        store.record_export_attempt({
            "report_id": entry_id,
            "package_version": package["package_version"],
            "digest": package["digest"],
            "evidence_count": len(evidence),
            "failures": summary["failures"],
        })
        store.log_event("review_package_exported", "govern_report", "quality_monitor", {
            "report_id": entry_id,
            "package_version": package["package_version"],
            "digest": package["digest"],
            "evidence_count": len(evidence),
        })

        late = [item for item in evidence if item["round"] > 1]
        late_note = f"，含迟到补查={len(late)} 笔（第2轮）" if late else ""
        return package, (
            f"复核包已导出：版本={package['package_version']}，证据明细={len(evidence)} 条"
            f"，失败={summary['failures']} 条{late_note}，摘要={package['digest'][:23]}…"
        )

    def export_attempts(self, entry_id: int) -> list[dict[str, Any]]:
        """查询报告的复核包导出尝试，供中断重试后核对复查范围。"""
        return store.export_attempts(entry_id)

    def execute_optimization(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """执行治理优化：根据报告建议触发实际的治理操作。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, "治理报告不存在"
        if entry.get("status") not in ("已生成", "已审批"):
            return None, f"治理报告状态为{entry.get('status')}，不可执行优化"

        # 触发血缘链路修复
        broken_lineages = [
            l for l in store.rows("lineage_graph")
            if l.get("status") == "链路断裂"
        ]
        for lin in broken_lineages:
            lin["pending"] = True

        # 触发质量监控重新检查
        quality_rules = store.rows("quality_monitor")
        for qr in quality_rules:
            if qr.get("status") == "已启用":
                qr["pending"] = True

        entry["status"] = "已执行"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["执行状态"] = "优化已执行"

        store.log_event("govern_optimization_executed", "govern_report", "govern_report", {
            "report_id": entry_id,
            "broken_lineages_triggered": len(broken_lineages),
            "quality_rules_retriggered": len(quality_rules),
        })

        return entry, f"治理优化已执行：触发 {len(broken_lineages)} 条血缘修复、{len(quality_rules)} 个质量规则"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "生成报告":
            return self.generate_report(entry_id)
        if action == "执行优化":
            return self.execute_optimization(entry_id)
        return super().run_action(entry_id, action)
