from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from .config import settings
from .models import Usage


class BudgetExceeded(RuntimeError):
    pass


def month():
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")


def overview(db):
    spent, reserved = db.execute(
        select(
            func.coalesce(func.sum(Usage.spent_yuan), 0), func.coalesce(func.sum(Usage.reserved_yuan), 0)
        ).where(Usage.month == month())
    ).one()
    total = settings.fixed_monthly_cost_yuan + float(spent) + float(reserved)
    return {
        "month": month(),
        "spent_yuan": round(float(spent), 4),
        "reserved_yuan": round(float(reserved), 4),
        "fixed_yuan": settings.fixed_monthly_cost_yuan,
        "projected_yuan": round(total, 4),
        "limit_yuan": settings.monthly_budget_yuan,
        "warning": total >= settings.budget_warning_yuan,
        "ai_enabled": settings.text_ai_enabled,
        "ai_mode": settings.ai_mode,
    }


def reserve(db, user_id, operation, amount, optional=False):
    # A single global lock serializes reservations across API/worker processes in production.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(83759201)"))
    projected = overview(db)["projected_yuan"]
    if projected + amount + settings.reserve_yuan > settings.monthly_budget_yuan or (
        optional and projected >= settings.budget_warning_yuan
    ):
        raise BudgetExceeded("本月 AI 额度不足，原始记录仍已保存")
    usage = Usage(user_id=user_id, operation=operation, month=month(), reserved_yuan=amount)
    db.add(usage)
    db.commit()
    return usage


def settle(db, usage, actual, latency_ms, failed=False):
    # Failed requests can still be billed: keep their reservation as a conservative spent estimate.
    usage.spent_yuan = max(0, usage.reserved_yuan if failed else actual)
    usage.reserved_yuan = 0
    usage.latency_ms = latency_ms
    usage.state = "failed" if failed else "settled"
    db.commit()
