"""质量报告复核包验收测试：销售/库存各一笔检查 → 生成报告 → 收妥迟到结果
→ 导出复核包 → 交给质量监控复查 → 复算失败条数 → 确认迟到检查轮次。

注意：store 是全局单例，本文件在 setup 中清空检查/复查/导出记录，
保证检查序号与轮次断言确定；不影响其他测试文件（它们不读这些记录）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.govern_report import GovernReportService
from app.services.lake_table import LakeTableService
from app.services.quality_monitor import QualityMonitorService
from app.services.review_package import package_digest, summarize_evidence
from app.store import store

SALES_TABLE = "销售订单表"
INVENTORY_TABLE = "库存快照表"


def _reset_review_state():
    store._quality_checks.clear()
    store._export_attempts.clear()
    store._review_runs.clear()
    store._review_records.clear()


def _register_table(lake_svc: LakeTableService, table_name: str) -> None:
    if store.find_by_field("lake_table", "表名", table_name):
        return
    entry, missing = lake_svc.create_entry({
        "表名": table_name,
        "所属业务域": "交易域" if "销售" in table_name else "仓储域",
        "存储格式": "iceberg",
        "分区键": "dt",
        "当前分片数": "1",
    })
    assert not missing and entry is not None
    result, msg = lake_svc.register_table(entry["id"])
    assert result is not None, f"湖表注册失败: {msg}"


def _create_enabled_rule(qm_svc: QualityMonitorService, rule_name: str,
                         table_name: str, threshold: str) -> int:
    entry, missing = qm_svc.create_entry({
        "规则名称": rule_name,
        "监控指标": "记录数",
        "告警阈值": threshold,
        "目标湖表": table_name,
        "检查频率": "每班一次",
    })
    assert not missing and entry is not None
    result, msg = qm_svc.enable_monitor(entry["id"])
    assert result is not None, f"监控规则启用失败: {msg}"
    return entry["id"]


def _create_report(gr_svc: GovernReportService) -> int:
    entry, missing = gr_svc.create_entry({
        "报告周期": "2026-11-22 白班",
        "治理指标": "待生成",
        "优化建议": "待生成",
    })
    assert not missing and entry is not None
    return entry["id"]


class TestReviewPackageFlow:
    """验收主流程：迟到检查进入补查轮，复查记录与导出文件对得上。"""

    def setup_method(self):
        _reset_review_state()
        self.lake_svc = LakeTableService()
        self.qm_svc = QualityMonitorService()
        self.gr_svc = GovernReportService()
        _register_table(self.lake_svc, SALES_TABLE)
        _register_table(self.lake_svc, INVENTORY_TABLE)
        # 销售规则阈值 2 > 湖表行数 1 → 检查失败；库存阈值 1 → 通过
        self.sales_rule = _create_enabled_rule(self.qm_svc, "销售记录数监控", SALES_TABLE, "2")
        self.inventory_rule = _create_enabled_rule(self.qm_svc, "库存记录数监控", INVENTORY_TABLE, "1")

    def _generate_report_with_two_checks(self) -> int:
        """按销售和库存各一笔检查生成报告。"""
        entry, msg = self.qm_svc.check_quality(self.sales_rule)
        assert entry is not None, msg
        entry, msg = self.qm_svc.check_quality(self.inventory_rule)
        assert entry is not None, msg

        report_id = _create_report(self.gr_svc)
        entry, msg = self.gr_svc.generate_report(report_id)
        assert entry is not None, msg
        assert entry["status"] == "已生成"
        return report_id

    def test_full_review_package_acceptance(self):
        report_id = self._generate_report_with_two_checks()

        # 报告已生成、文件还没导出时，库存表的另一次检查迟到（交班前收妥）
        late_entry, msg = self.qm_svc.check_quality(self.inventory_rule)
        assert late_entry is not None, msg
        assert len(store.quality_checks()) == 3

        # 下载复核包
        package, msg = self.gr_svc.build_review_package(report_id)
        assert package is not None, msg

        # 摘要、证据明细、规则来源来自同一版本
        assert package["package_version"] == f"govrep-{report_id}-v1"
        assert package["source_report"]["report_id"] == report_id
        assert len(package["evidence"]) == 3
        assert {r["rule_id"] for r in package["rule_source"]} == {
            self.sales_rule, self.inventory_rule,
        }
        assert set(package["target_tables"]) == {SALES_TABLE, INVENTORY_TABLE}

        # 按文件中的检查明细复算失败条数，与包内摘要一致
        recomputed = summarize_evidence(package["evidence"])
        assert recomputed["failures"] == package["summary"]["failures"] == 1
        assert recomputed["quality_checks"] == package["summary"]["quality_checks"] == 3
        assert recomputed["passes"] == package["summary"]["passes"] == 2

        # 迟到检查被安排在第2轮（交班前补查）
        late_items = [e for e in package["evidence"] if e["check_id"] == 3]
        assert len(late_items) == 1 and late_items[0]["round"] == 2
        assert "补查" in late_items[0]["round_label"]

        # 交给湖内质量监控：读取复核包生成复查记录
        run, msg = self.qm_svc.review_package(package)
        assert run is not None, msg
        assert run["digest_verified"] is True
        assert run["counts_match"] is True
        assert run["recomputed_failures"] == 1

        # 值班员把复查记录数与导出数据重新对一遍
        records = store.review_records(run["review_run_id"])
        assert len(records) == len(package["evidence"]) == run["review_record_count"]

        # 复查记录关联了表、规则和来源报表
        sales_record = next(r for r in records if r["table_name"] == SALES_TABLE)
        assert sales_record["rule_name"] == "销售记录数监控"
        assert sales_record["lake_table_status"] == "已注册"
        assert sales_record["report_status"] == "已生成"
        assert sales_record["passed"] is False

        # 在复查记录里确认迟到检查安排在哪次
        late_record = next(r for r in records if r["check_id"] == 3)
        assert late_record["check_round"] == 2
        assert "补查" in late_record["round_label"]

    def test_export_retry_digest_detects_scope_change(self):
        """导出中断后重试：范围未变摘要一致，迟到新检查后摘要变化可核对。"""
        report_id = self._generate_report_with_two_checks()

        first, _ = self.gr_svc.build_review_package(report_id)
        retry, _ = self.gr_svc.build_review_package(report_id)
        assert first["package_version"] == retry["package_version"]
        assert first["digest"] == retry["digest"], "范围未变时重试摘要应一致"

        # 重试之后又收妥一笔迟到检查，复查范围改变
        self.qm_svc.check_quality(self.sales_rule)
        third, _ = self.gr_svc.build_review_package(report_id)
        assert third["package_version"] == first["package_version"], "同一报告同一版本"
        assert third["digest"] != first["digest"], "范围变化后摘要应变化"
        assert third["summary"]["quality_checks"] == 3

        attempts = self.gr_svc.export_attempts(report_id)
        assert len(attempts) == 3
        digests = {a["digest"] for a in attempts}
        assert len(digests) == 2, "三次导出应只出现两种摘要"

    def test_review_uses_packaged_rule_source(self):
        """接收端读到的规则来源与导出文件同一版本：导出后改规则不影响复查。"""
        report_id = self._generate_report_with_two_checks()
        package, _ = self.gr_svc.build_review_package(report_id)

        # 导出后有人改了湖内规则阈值，复查仍应以包内快照为准
        live_rule = store.find("quality_monitor", self.sales_rule)
        live_rule["告警阈值"] = "999"

        run, msg = self.qm_svc.review_package(package)
        assert run is not None, msg
        records = store.review_records(run["review_run_id"])
        sales_record = next(r for r in records if r["rule_id"] == self.sales_rule)
        assert sales_record["rule_threshold"] == "2"

    def test_review_rejects_invalid_package(self):
        run, msg = self.qm_svc.review_package({"package_type": "别的文件"})
        assert run is None and "复核包" in msg
        run, msg = self.qm_svc.review_package({
            "package_type": "quality_review_package",
            "source_report": {},
        })
        assert run is None and "缺少" in msg

    def test_package_requires_generated_report(self):
        report_id = _create_report(self.gr_svc)
        package, msg = self.gr_svc.build_review_package(report_id)
        assert package is None and "尚未生成" in msg

    def test_tampered_package_digest_fails_verification(self):
        """包内明细被改动时，接收端摘要校验应报警。"""
        report_id = self._generate_report_with_two_checks()
        package, _ = self.gr_svc.build_review_package(report_id)
        package["evidence"][0]["passed"] = True  # 篡改明细
        run, msg = self.qm_svc.review_package(package)
        assert run is not None
        assert run["digest_verified"] is False
        assert "不一致" in msg
        # 复算失败条数以文件明细为准：篡改后失败数随之变化
        assert run["recomputed_failures"] == summarize_evidence(package["evidence"])["failures"]
