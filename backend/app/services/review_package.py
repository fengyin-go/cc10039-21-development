"""质量报告复核包共享逻辑：包结构、轮次标签、内容摘要与指标复算。

治理报告导出复核包、质量监控读取复核包都走这里的同一套规则，
保证"导出端"和"接收端"对同一版本、同一份证据的理解一致。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

PACKAGE_TYPE = "quality_review_package"

# 检查轮次：第 1 轮为报告生成时已纳入的检查，第 2 轮为报告生成后、
# 文件导出前迟到的补查（交班前收妥的迟到结果）。
ROUND_LABELS: dict[int, str] = {
    1: "第1轮（报告生成）",
    2: "第2轮（交班前补查）",
}

# 参与内容摘要计算的字段：摘要、证据明细、规则来源等同版本内容。
# exported_at 等每次导出都会变的元信息不参与，保证中断重试后摘要可比对。
_DIGEST_FIELDS = (
    "package_version",
    "source_report",
    "summary",
    "evidence",
    "rule_source",
    "target_tables",
)


def package_digest(package: dict[str, Any]) -> str:
    """对复核包同版本内容计算 sha256 摘要，用于核对复查范围是否变化。"""
    content = {field: package.get(field) for field in _DIGEST_FIELDS}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summarize_evidence(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """按检查明细复算治理质量指标：检查总数、失败条数、通过条数和轮次分布。"""
    failures = sum(1 for item in evidence if not item.get("passed"))
    passes = sum(1 for item in evidence if item.get("passed"))
    rounds: dict[int, int] = {}
    for item in evidence:
        round_no = int(item.get("round", 1))
        rounds[round_no] = rounds.get(round_no, 0) + 1
    return {
        "quality_checks": len(evidence),
        "failures": failures,
        "passes": passes,
        "rounds": rounds,
    }
