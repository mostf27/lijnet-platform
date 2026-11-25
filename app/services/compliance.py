from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order, OrderStatus, OriginType, Product
from app.schemas import LocalComplianceReport


def calculate_local_content_compliance(db: Session) -> LocalComplianceReport:
    """
    Calculates LOCAL_ETHIOPIAN vs IMPORTED catalog share against Ethiopia's
    Electronic Transaction Proclamation No. 1205/2020 (>= 50% local offerings).
    """
    total_active = db.scalar(select(func.count(Product.id)).where(Product.is_active.is_(True))) or 0

    local_count = (
        db.scalar(
            select(func.count(Product.id)).where(
                Product.is_active.is_(True),
                Product.origin_type == OriginType.LOCAL_ETHIOPIAN,
            )
        )
        or 0
    )

    imported_count = total_active - local_count
    local_pct = round((local_count / total_active) * 100.0, 1) if total_active > 0 else 0.0
    imported_pct = round((imported_count / total_active) * 100.0, 1) if total_active > 0 else 0.0

    return LocalComplianceReport(
        total_active_products=total_active,
        local_ethiopian_count=local_count,
        imported_count=imported_count,
        local_percentage=local_pct,
        imported_percentage=imported_pct,
        min_required_percentage=settings.LOCAL_CONTENT_MIN_PCT,
        is_compliant=(local_pct >= settings.LOCAL_CONTENT_MIN_PCT) if total_active > 0 else False,
    )


def evaluate_return_eligibility(
    order: Order,
    hygiene_seal_broken: bool = False,
) -> tuple[bool, str]:
    """
    Enforces Ethiopia's statutory 7-day cooling-off return period under
    Proclamation No. 1205/2020 while blocking opened sealed hygiene consumables.
    """
    if order.status == OrderStatus.RETURN_REQUESTED:
        return False, "A return request has already been submitted for this order."

    if order.status == OrderStatus.REFUNDED:
        return False, "This order has already been refunded."

    if order.status != OrderStatus.DELIVERED or order.delivered_at is None:
        return False, "Returns can only be requested after the order has been marked as DELIVERED."

    delivered_utc = (
        order.delivered_at
        if order.delivered_at.tzinfo is not None
        else order.delivered_at.replace(tzinfo=timezone.utc)
    )
    deadline = delivered_utc + timedelta(days=settings.STATUTORY_RETURN_DAYS)
    now = datetime.now(timezone.utc)

    if now > deadline:
        days_elapsed = (now - delivered_utc).days
        return (
            False,
            f"Statutory {settings.STATUTORY_RETURN_DAYS}-day cooling-off return window expired "
            f"({days_elapsed} days since delivery).",
        )

    has_sealed_hygiene = any(item.product.is_hygiene_sealed for item in order.items)
    if has_sealed_hygiene and hygiene_seal_broken:
        return (
            False,
            "Opened hygiene consumables (diapers, wipes, feeding bottles) are ineligible for return "
            "under health & child-safety regulations.",
        )

    days_left = max((deadline - now).days, 0)
    return (
        True,
        f"Eligible under Proclamation No. 1205/2020 ({days_left} day(s) remaining in cooling-off window).",
    )
