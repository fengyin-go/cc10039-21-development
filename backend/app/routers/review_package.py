"""质量报告复核包端点：治理报告导出复核包，质量监控读取复核包生成复查记录。

- GET  /api/govern_report/{report_id}/review-package          下载复核包（JSON 文件）
- GET  /api/govern_report/{report_id}/review-package/attempts 查询导出尝试，核对复查范围
- POST /api/quality_monitor/review-packages                   提交复核包，生成一轮复查记录
- GET  /api/quality_monitor/review-records                    查询复查记录，与导出数据对账

注意：本路由必须注册在通用资源路由之前，否则
/api/quality_monitor/review-records 会被 /{module}/{entry_id} 抢先匹配。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Response

from app.schemas import ActionResult
from app.services import service_for
from app.services.govern_report import GovernReportService
from app.services.quality_monitor import QualityMonitorService

router = APIRouter(prefix="/api", tags=["质量报告复核包"])


def _govern_service() -> GovernReportService:
    service = service_for("govern_report")
    assert isinstance(service, GovernReportService)
    return service


def _quality_service() -> QualityMonitorService:
    service = service_for("quality_monitor")
    assert isinstance(service, QualityMonitorService)
    return service


@router.get("/govern_report/{report_id}/review-package")
def download_review_package(report_id: int) -> Response:
    """下载质量报告复核包：报告摘要、证据明细、规则来源同一版本。"""
    package, message = _govern_service().build_review_package(report_id)
    if package is None:
        raise HTTPException(status_code=409, detail=message)
    # 值班员会直接打开文件对账，导出为带缩进的明文 JSON
    content = json.dumps(package, ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="govern_report_{report_id}_review_package.json"'
            ),
            "X-Review-Package-Version": str(package["package_version"]),
            "X-Review-Package-Digest": str(package["digest"]),
        },
    )


@router.get("/govern_report/{report_id}/review-package/attempts")
def list_export_attempts(report_id: int) -> dict[str, Any]:
    """查询报告的复核包导出尝试：中断重试后比对内容摘要，核对复查范围是否改变。"""
    attempts = _govern_service().export_attempts(report_id)
    digests = [a.get("digest") for a in attempts]
    return {
        "report_id": report_id,
        "total": len(attempts),
        "items": attempts,
        "scope_changed": len(set(digests)) > 1,
    }


@router.post("/quality_monitor/review-packages", response_model=ActionResult)
def submit_review_package(package: dict[str, Any] = Body(...)) -> ActionResult:
    """质量监控读取复核包：关联表、规则和来源报表，生成一轮复查记录。"""
    run, message = _quality_service().review_package(package)
    return ActionResult(ok=run is not None, message=message, entry=run)


@router.get("/quality_monitor/review-records")
def list_review_records(
    review_run_id: int | None = Query(default=None),
) -> dict[str, Any]:
    """查询复查记录：值班员把记录数与导出文件里的证据明细重新对一遍。"""
    from app.store import store

    records = store.review_records(review_run_id)
    runs = store.review_runs()
    return {
        "total": len(records),
        "items": records,
        "runs": runs,
    }
