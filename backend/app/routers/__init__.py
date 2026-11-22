"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, cross_module, review_package

# review_package 的固定路径（如 /api/quality_monitor/review-records）
# 必须先于 resource 的 /{module}/{entry_id} 注册，否则会被动态路径抢先匹配。
ROUTERS = [review_package, resource, cross_module]