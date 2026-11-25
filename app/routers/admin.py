import calendar
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import ADDIS_SUBCITY_FEES, settings
from app.database import get_db
from app.models import (
    AgeRange,
    Order,
    OrderItem,
    OrderStatus,
    OriginType,
    PaymentStatus,
    Product,
    ProductCategory,
    User,
    UserRole,
)
from app.services.auth import authenticate_user, require_admin_session
from app.services.compliance import calculate_local_content_compliance
from app.services.i18n import build_cart_summary, get_lang, get_translations, templates
from app.services.invoices import _split_vat_inclusive
from app.services.platform_settings import get_effective_settings, save_platform_settings
from app.services.telegram_bot import TelegramBotClient, notify_order_event

router = APIRouter(prefix="/admin", tags=["Admin & Courier Dispatch"])

EAT = timezone(timedelta(hours=3))


def _to_eat(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(EAT)
    return dt.astimezone(EAT)


def _resolve_report_window(
    period: str,
    report_date: str | None,
    report_week: str | None,
    report_month: str | None,
    report_year: int | None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Resolves Daily, Weekly, Monthly, Yearly, or All-Time date boundaries in EAT."""
    today_eat = datetime.now(EAT).date()
    normalized_period = (period or "daily").strip().lower()
    if scope == "all":
        normalized_period = "all"
    if normalized_period not in {"daily", "weekly", "monthly", "yearly", "all"}:
        normalized_period = "daily"

    # Parse individual period inputs with safe fallbacks
    parsed_day = today_eat
    if report_date:
        try:
            parsed_day = date.fromisoformat(report_date.strip())
        except ValueError:
            parsed_day = today_eat

    iso_year, iso_week, _ = today_eat.isocalendar()
    parsed_week_str = f"{iso_year}-W{iso_week:02d}"
    week_start = date.fromisocalendar(iso_year, iso_week, 1)
    week_end = date.fromisocalendar(iso_year, iso_week, 7)
    if report_week and "-W" in report_week:
        try:
            wy_str, ww_str = report_week.strip().split("-W", 1)
            wy, ww = int(wy_str), int(ww_str)
            week_start = date.fromisocalendar(wy, ww, 1)
            week_end = date.fromisocalendar(wy, ww, 7)
            parsed_week_str = f"{wy}-W{ww:02d}"
        except ValueError:
            pass

    parsed_month_str = today_eat.strftime("%Y-%m")
    month_start = today_eat.replace(day=1)
    month_end = today_eat.replace(day=calendar.monthrange(today_eat.year, today_eat.month)[1])
    if report_month and "-" in report_month:
        try:
            my_str, mm_str = report_month.strip().split("-", 1)
            my, mm = int(my_str), int(mm_str)
            last_d = calendar.monthrange(my, mm)[1]
            month_start = date(my, mm, 1)
            month_end = date(my, mm, last_d)
            parsed_month_str = f"{my:04d}-{mm:02d}"
        except ValueError:
            pass

    parsed_year = today_eat.year
    if report_year and 2000 <= report_year <= 2100:
        parsed_year = report_year
    year_start = date(parsed_year, 1, 1)
    year_end = date(parsed_year, 12, 31)

    if normalized_period == "daily":
        start_dt: date | None = parsed_day
        end_dt: date | None = parsed_day
        title = "Daily Sales Report"
        display_range = parsed_day.strftime("%d %b %Y")
        file_suffix = f"daily_{parsed_day.isoformat()}"
    elif normalized_period == "weekly":
        start_dt = week_start
        end_dt = week_end
        title = "Weekly Sales Report"
        display_range = (
            f"{parsed_week_str} ({week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')})"
        )
        file_suffix = f"weekly_{parsed_week_str}"
    elif normalized_period == "monthly":
        start_dt = month_start
        end_dt = month_end
        title = "Monthly Sales Report"
        display_range = month_start.strftime("%B %Y")
        file_suffix = f"monthly_{parsed_month_str}"
    elif normalized_period == "yearly":
        start_dt = year_start
        end_dt = year_end
        title = "Yearly Sales Report"
        display_range = f"Year {parsed_year}"
        file_suffix = f"yearly_{parsed_year}"
    else:
        start_dt = None
        end_dt = None
        title = "All-Time Sales Ledger"
        display_range = "All Recorded Dates"
        file_suffix = "all_dates"

    return {
        "period": normalized_period,
        "title": title,
        "display_range": display_range,
        "file_suffix": file_suffix,
        "start_date": start_dt,
        "end_date": end_dt,
        "today_iso": today_eat.isoformat(),
        "current_week_iso": f"{iso_year}-W{iso_week:02d}",
        "current_month_iso": today_eat.strftime("%Y-%m"),
        "current_year": today_eat.year,
        "report_date_iso": parsed_day.isoformat(),
        "report_week_iso": parsed_week_str,
        "report_month_iso": parsed_month_str,
        "report_year": parsed_year,
    }


def _build_sales_ledger(
    orders: list[Order],
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    """
    Constructs a chronological itemized sales & receivable ledger in EAT (UTC+3).
    - Beginning Balance: cumulative uncollected balance prior to start_date
    - Debit: gross line sales billed (incl. 15% VAT)
    - Credit: settled/paid amount collected
    - Balance: running balance = beginning_balance + debit - credit
    """
    chronological_orders = sorted(
        [o for o in orders if o.status != OrderStatus.CANCELLED],
        key=lambda o: (_to_eat(o.created_at), o.id),
    )

    running_balance = Decimal("0.00")
    opening_balance = Decimal("0.00")
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    total_vat = Decimal("0.00")
    total_qty = 0
    rows: list[dict[str, Any]] = []

    for order in chronological_orders:
        order_dt_eat = _to_eat(order.created_at)
        order_day = order_dt_eat.date()
        is_paid = order.payment_status == PaymentStatus.PAID
        fiscal_no = f"FS-{order_dt_eat.year}-{order.id:06d}"

        line_entries: list[dict[str, Any]] = []
        for item in sorted(order.items, key=lambda i: i.id):
            line_entries.append(
                {
                    "sku": item.product.sku,
                    "title_en": item.product.title_en,
                    "title_am": item.product.title_am,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price_etb,
                    "gross_amount": item.line_total_etb,
                }
            )

        if order.delivery_fee_etb > Decimal("0.00"):
            sub_city = order.delivery_address.sub_city if order.delivery_address else "Addis Ababa"
            line_entries.append(
                {
                    "sku": "DELIV-ADD",
                    "title_en": f"Courier Delivery ({sub_city})",
                    "title_am": f"የአዲስ አበባ መላኪያ ({sub_city})",
                    "quantity": 1,
                    "unit_price": order.delivery_fee_etb,
                    "gross_amount": order.delivery_fee_etb,
                }
            )

        for entry in line_entries:
            debit = entry["gross_amount"]
            credit = debit if is_paid else Decimal("0.00")
            _, line_vat = _split_vat_inclusive(debit, settings.VAT_RATE)

            if start_date is not None and order_day < start_date:
                running_balance += debit - credit
                opening_balance = running_balance
                continue

            if end_date is not None and order_day > end_date:
                continue

            beg_bal = running_balance
            end_bal = beg_bal + debit - credit
            running_balance = end_bal

            total_debit += debit
            total_credit += credit
            total_vat += line_vat
            total_qty += int(entry["quantity"])

            rows.append(
                {
                    "timestamp_eat": order_dt_eat.strftime("%Y-%m-%d %H:%M"),
                    "order_number": order.order_number,
                    "fiscal_no": fiscal_no,
                    "sku": entry["sku"],
                    "title_en": entry["title_en"],
                    "title_am": entry["title_am"],
                    "quantity": int(entry["quantity"]),
                    "unit_price_etb": entry["unit_price"],
                    "beginning_balance_etb": beg_bal,
                    "debit_etb": debit,
                    "credit_etb": credit,
                    "balance_etb": end_bal,
                    "vat_15_etb": line_vat,
                    "payment_method": order.payment_method.value,
                    "payment_status": order.payment_status.value,
                    "order_status": order.status.value,
                    "customer_name": (
                        order.delivery_address.recipient_name
                        if order.delivery_address
                        else "Customer"
                    ),
                    "sub_city": (
                        order.delivery_address.sub_city if order.delivery_address else "Addis Ababa"
                    ),
                }
            )

    return {
        "rows": rows,
        "opening_balance_etb": opening_balance,
        "total_debit_etb": total_debit,
        "total_credit_etb": total_credit,
        "closing_balance_etb": running_balance,
        "total_vat_etb": total_vat,
        "total_qty": total_qty,
    }


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(
    request: Request,
    next_url: str = Query(default="/admin", alias="next"),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if request.session.get("admin_user_id"):
        return RedirectResponse(url="/admin", status_code=303)

    lang = get_lang(request)
    t = get_translations(lang)
    cart = build_cart_summary(request, db, sub_city=request.session.get("sub_city", "Bole"))

    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={
            "lang": lang,
            "t": t,
            "cart": cart,
            "next_url": next_url if next_url.startswith("/admin") else "/admin",
            "error": error,
        },
    )


@router.post("/login")
async def admin_login_submit(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/admin"),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, identifier, password)
    if not user or user.role != UserRole.ADMIN:
        return RedirectResponse(
            url="/admin/login?error=Invalid+admin+credentials+or+insufficient+permissions.",
            status_code=303,
        )

    request.session["admin_user_id"] = user.id
    safe_next = next_url if next_url.startswith("/admin") else "/admin"
    return RedirectResponse(url=safe_next, status_code=303)


@router.post("/logout")
async def admin_logout(request: Request):
    request.session.pop("admin_user_id", None)
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    message: str | None = Query(default=None),
    period: str = Query(default="daily"),
    report_date: str | None = Query(default=None),
    report_week: str | None = Query(default=None),
    report_month: str | None = Query(default=None),
    report_year: int | None = Query(default=None),
    scope: str | None = Query(default=None),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    lang = get_lang(request)
    t = get_translations(lang)
    effective_cfg = get_effective_settings()
    compliance = calculate_local_content_compliance(db)

    products = db.scalars(select(Product).order_by(Product.created_at.desc())).all()
    orders = db.scalars(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .order_by(Order.created_at.desc())
        .limit(500)
    ).all()
    customers = db.scalars(
        select(User).where(User.role == UserRole.CUSTOMER).order_by(User.created_at.desc())
    ).all()
    vendors = db.scalars(select(User).where(User.role.in_([UserRole.VENDOR, UserRole.ADMIN]))).all()

    # --- Compute Live Dashboard & Reporting KPIs ---
    active_orders = [
        o for o in orders if o.status not in (OrderStatus.CANCELLED, OrderStatus.REFUNDED)
    ]
    total_revenue_etb = sum((o.total_etb for o in active_orders), Decimal("0.00"))
    delivery_fees_etb = sum((o.delivery_fee_etb for o in active_orders), Decimal("0.00"))
    net_excl_vat_etb, vat_collected_etb = _split_vat_inclusive(total_revenue_etb, settings.VAT_RATE)

    paid_revenue_etb = sum(
        (o.total_etb for o in active_orders if o.payment_status == PaymentStatus.PAID),
        Decimal("0.00"),
    )
    unpaid_revenue_etb = sum(
        (o.total_etb for o in active_orders if o.payment_status == PaymentStatus.UNPAID),
        Decimal("0.00"),
    )

    aov_etb = (
        (total_revenue_etb / Decimal(len(active_orders))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if active_orders
        else Decimal("0.00")
    )
    paid_orders_count = sum(1 for o in active_orders if o.payment_status == PaymentStatus.PAID)
    paid_rate_pct = (
        round((paid_orders_count / len(active_orders)) * 100, 1) if active_orders else 0.0
    )

    returned_orders_count = sum(
        1 for o in orders if o.status in (OrderStatus.RETURN_REQUESTED, OrderStatus.REFUNDED)
    )
    return_rate_pct = round((returned_orders_count / len(orders)) * 100, 1) if orders else 0.0

    pending_delivery = sum(
        1
        for o in orders
        if o.status in (OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.OUT_FOR_DELIVERY)
    )
    free_deliv_orders = sum(1 for o in active_orders if o.delivery_fee_etb == Decimal("0.00"))
    free_delivery_pct = (
        round((free_deliv_orders / len(active_orders)) * 100) if active_orders else 0
    )

    monthly_target_etb = Decimal("150000.00")
    monthly_target_pct = min(round(float((total_revenue_etb / monthly_target_etb) * 100), 1), 100.0)

    # --- Period-Aware Sales Report Ledger (Daily / Weekly / Monthly / Yearly) ---
    today_eat = datetime.now(EAT).date()
    latest_sale_date = (
        max(_to_eat(o.created_at).date() for o in orders if o.status != OrderStatus.CANCELLED)
        if any(o.status != OrderStatus.CANCELLED for o in orders)
        else today_eat
    )

    report_window = _resolve_report_window(
        period=period,
        report_date=report_date,
        report_week=report_week,
        report_month=report_month,
        report_year=report_year,
        scope=scope,
    )
    sales_ledger = _build_sales_ledger(
        orders,
        start_date=report_window["start_date"],
        end_date=report_window["end_date"],
    )

    # --- Last 8 Days Sales Trend Series ---
    today_utc = datetime.now(timezone.utc).date()
    day_buckets: dict[str, Decimal] = {}
    for offset in range(7, -1, -1):
        day_dt = today_utc - timedelta(days=offset)
        day_buckets[day_dt.strftime("%d %b")] = Decimal("0.00")

    for o in active_orders:
        if o.created_at:
            key = o.created_at.astimezone(timezone.utc).strftime("%d %b")
            if key in day_buckets:
                day_buckets[key] += o.total_etb

    chart_labels = list(day_buckets.keys())
    chart_values = [float(v) for v in day_buckets.values()]

    # --- Analytics: Payment Method Breakdown ---
    pay_totals: dict[str, Decimal] = {}
    pay_counts: dict[str, int] = {}
    for o in active_orders:
        pm = o.payment_method.value
        pay_totals[pm] = pay_totals.get(pm, Decimal("0.00")) + o.total_etb
        pay_counts[pm] = pay_counts.get(pm, 0) + 1

    payment_breakdown = []
    for pm, amt in sorted(pay_totals.items(), key=lambda x: x[1], reverse=True):
        share = (
            round(float((amt / total_revenue_etb) * 100), 1)
            if total_revenue_etb > Decimal("0.00")
            else 0.0
        )
        payment_breakdown.append(
            {
                "method": pm,
                "count": pay_counts[pm],
                "total_etb": amt,
                "share_pct": share,
            }
        )

    # --- Analytics: Addis Ababa Sub-City Breakdown ---
    sc_totals: dict[str, Decimal] = {}
    sc_counts: dict[str, int] = {}
    for o in active_orders:
        sc = o.delivery_address.sub_city if o.delivery_address else "Unknown"
        sc_totals[sc] = sc_totals.get(sc, Decimal("0.00")) + o.total_etb
        sc_counts[sc] = sc_counts.get(sc, 0) + 1

    subcity_breakdown = []
    for sc, amt in sorted(sc_totals.items(), key=lambda x: x[1], reverse=True):
        share = (
            round(float((amt / total_revenue_etb) * 100), 1)
            if total_revenue_etb > Decimal("0.00")
            else 0.0
        )
        subcity_breakdown.append(
            {
                "sub_city": sc,
                "count": sc_counts[sc],
                "total_etb": amt,
                "share_pct": share,
            }
        )

    # --- Analytics: Sales by Product Category ---
    cat_totals: dict[str, Decimal] = {c.value: Decimal("0.00") for c in ProductCategory}
    for o in active_orders:
        for item in o.items:
            cat_key = item.product.category.value
            cat_totals[cat_key] = cat_totals.get(cat_key, Decimal("0.00")) + item.line_total_etb

    # --- Top Selling Products ---
    sold_rows = db.execute(
        select(
            Product,
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units_sold"),
        )
        .outerjoin(OrderItem, OrderItem.product_id == Product.id)
        .where(Product.is_active.is_(True))
        .group_by(Product.id)
        .order_by(func.coalesce(func.sum(OrderItem.quantity), 0).desc(), Product.id.asc())
        .limit(5)
    ).all()

    top_products = [
        {
            "id": prod.id,
            "sku": prod.sku,
            "title_en": prod.title_en,
            "image_url": prod.image_url,
            "price_etb": prod.price_etb,
            "units_sold": int(units_sold),
            "is_local": prod.origin_type == OriginType.LOCAL_ETHIOPIAN,
        }
        for prod, units_sold in sold_rows
    ]

    inventory_value_etb = sum(
        (p.price_etb * p.stock_count for p in products if p.is_active),
        Decimal("0.00"),
    )

    kpis = {
        "total_revenue_etb": total_revenue_etb,
        "delivery_fees_etb": delivery_fees_etb,
        "net_excl_vat_etb": net_excl_vat_etb,
        "vat_collected_etb": vat_collected_etb,
        "paid_revenue_etb": paid_revenue_etb,
        "unpaid_revenue_etb": unpaid_revenue_etb,
        "aov_etb": aov_etb,
        "paid_rate_pct": paid_rate_pct,
        "return_rate_pct": return_rate_pct,
        "returned_orders_count": returned_orders_count,
        "total_orders": len(orders),
        "total_customers": len(customers),
        "pending_delivery": pending_delivery,
        "monthly_target_etb": monthly_target_etb,
        "monthly_target_pct": monthly_target_pct,
        "free_delivery_pct": free_delivery_pct,
        "inventory_value_etb": inventory_value_etb,
    }

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "lang": lang,
            "t": t,
            "admin_user": admin_user,
            "compliance": compliance,
            "kpis": kpis,
            "today_label": today_eat.strftime("%d %b %Y"),
            "latest_sale_date_iso": latest_sale_date.isoformat(),
            "report_window": report_window,
            "sales_ledger": sales_ledger,
            "chart_labels_json": json.dumps(chart_labels),
            "chart_values_json": json.dumps(chart_values),
            "payment_labels_json": json.dumps([p["method"] for p in payment_breakdown]),
            "payment_values_json": json.dumps([float(p["total_etb"]) for p in payment_breakdown]),
            "payment_breakdown": payment_breakdown,
            "subcity_labels_json": json.dumps([s["sub_city"] for s in subcity_breakdown]),
            "subcity_values_json": json.dumps([float(s["total_etb"]) for s in subcity_breakdown]),
            "subcity_breakdown": subcity_breakdown,
            "category_labels_json": json.dumps(list(cat_totals.keys())),
            "category_values_json": json.dumps([float(v) for v in cat_totals.values()]),
            "top_products": top_products,
            "products": products,
            "orders": orders,
            "customers": customers,
            "vendors": vendors,
            "categories": [c.value for c in ProductCategory],
            "age_ranges": [a.value for a in AgeRange],
            "origin_types": [o.value for o in OriginType],
            "order_statuses": [s.value for s in OrderStatus],
            "settings_data": effective_cfg,
            "message": message,
        },
    )


@router.post("/products/create")
async def create_bilingual_product(
    sku: str = Form(...),
    title_en: str = Form(...),
    title_am: str = Form(...),
    description_en: str = Form(...),
    description_am: str = Form(...),
    category: str = Form(...),
    age_range: str = Form(...),
    origin_type: str = Form(...),
    price_etb: Decimal = Form(...),
    stock_count: int = Form(...),
    vendor_id: int = Form(...),
    image_url: str = Form(""),
    safety_certified: bool = Form(True),
    is_hygiene_sealed: bool = Form(False),
    is_starter_bundle: bool = Form(False),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    product = Product(
        sku=sku.strip().upper(),
        title_en=title_en.strip(),
        title_am=title_am.strip(),
        description_en=description_en.strip(),
        description_am=description_am.strip(),
        category=ProductCategory(category),
        age_range=AgeRange(age_range),
        origin_type=OriginType(origin_type),
        price_etb=price_etb,
        stock_count=stock_count,
        vendor_id=vendor_id,
        image_url=image_url.strip() or "/static/images/default-product.svg",
        safety_certified=safety_certified,
        is_hygiene_sealed=is_hygiene_sealed,
        is_starter_bundle=is_starter_bundle,
    )
    db.add(product)
    db.commit()
    return RedirectResponse(
        url="/admin?message=Bilingual+product+created+successfully.#products",
        status_code=303,
    )


@router.post("/products/{product_id}/stock")
async def update_product_stock(
    product_id: int,
    stock_count: int = Form(...),
    price_etb: Decimal = Form(...),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    product.stock_count = max(stock_count, 0)
    product.price_etb = price_etb
    db.commit()
    return RedirectResponse(
        url="/admin?message=Inventory+updated+successfully.#inventory",
        status_code=303,
    )


@router.post("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    order = db.scalar(
        select(Order).options(selectinload(Order.delivery_address)).where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    new_status = OrderStatus(status)
    order.status = new_status
    if new_status == OrderStatus.DELIVERED and order.delivered_at is None:
        order.delivered_at = datetime.now(timezone.utc)
        if order.payment_status == PaymentStatus.UNPAID:
            order.payment_status = PaymentStatus.PAID
    elif new_status == OrderStatus.REFUNDED:
        order.payment_status = PaymentStatus.REFUNDED

    db.commit()
    await notify_order_event(db, order, f"Order Status Updated: {new_status.value}")

    return RedirectResponse(
        url=f"/admin?message=Order+{order.order_number}+updated+to+{new_status.value}.#orders",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# REPORTING EXPORTS (Daily/Weekly/Monthly/Yearly Ledger, MoR VAT, Inventory)
# ---------------------------------------------------------------------------


def _stream_sales_ledger_csv(
    db: Session,
    period: str,
    report_date: str | None,
    report_week: str | None,
    report_month: str | None,
    report_year: int | None,
    scope: str | None,
) -> StreamingResponse:
    orders = db.scalars(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .order_by(Order.created_at.asc(), Order.id.asc())
    ).all()

    window = _resolve_report_window(
        period=period,
        report_date=report_date,
        report_week=report_week,
        report_month=report_month,
        report_year=report_year,
        scope=scope,
    )
    ledger = _build_sales_ledger(
        orders,
        start_date=window["start_date"],
        end_date=window["end_date"],
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Report Period",
            window["title"],
            "Range",
            window["display_range"],
        ]
    )
    writer.writerow([])
    writer.writerow(
        [
            "Date & Time (EAT)",
            "Order Number",
            "Fiscal Invoice No",
            "SKU",
            "Sales Item (EN)",
            "Sales Item (AM)",
            "Quantity",
            "Unit Price (ETB)",
            "Beginning Balance (ETB)",
            "Debit - Sales Billed (ETB)",
            "Credit - Paid/Settled (ETB)",
            "Balance (ETB)",
            "15% VAT Included (ETB)",
            "Payment Method",
            "Payment Status",
            "Customer Name",
            "Sub-City",
        ]
    )

    for row in ledger["rows"]:
        writer.writerow(
            [
                row["timestamp_eat"],
                row["order_number"],
                row["fiscal_no"],
                row["sku"],
                row["title_en"],
                row["title_am"],
                row["quantity"],
                f"{row['unit_price_etb']:.2f}",
                f"{row['beginning_balance_etb']:.2f}",
                f"{row['debit_etb']:.2f}",
                f"{row['credit_etb']:.2f}",
                f"{row['balance_etb']:.2f}",
                f"{row['vat_15_etb']:.2f}",
                row["payment_method"],
                row["payment_status"],
                row["customer_name"],
                row["sub_city"],
            ]
        )

    writer.writerow([])
    writer.writerow(
        [
            "PERIOD TOTALS",
            "",
            "",
            "",
            "",
            "",
            ledger["total_qty"],
            "",
            f"{ledger['opening_balance_etb']:.2f}",
            f"{ledger['total_debit_etb']:.2f}",
            f"{ledger['total_credit_etb']:.2f}",
            f"{ledger['closing_balance_etb']:.2f}",
            f"{ledger['total_vat_etb']:.2f}",
            "",
            "",
            "",
            "",
        ]
    )

    output.seek(0)
    filename = f"lijnet_sales_report_{window['file_suffix']}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/sales-ledger-csv")
async def export_period_sales_ledger_csv(
    period: str = Query(default="daily"),
    report_date: str | None = Query(default=None),
    report_week: str | None = Query(default=None),
    report_month: str | None = Query(default=None),
    report_year: int | None = Query(default=None),
    scope: str | None = Query(default=None),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    """Exports the itemized Daily, Weekly, Monthly, or Yearly Sales Report CSV."""
    return _stream_sales_ledger_csv(
        db=db,
        period=period,
        report_date=report_date,
        report_week=report_week,
        report_month=report_month,
        report_year=report_year,
        scope=scope,
    )


@router.get("/reports/daily-sales-csv")
async def export_daily_sales_report_csv(
    report_date: str | None = Query(default=None),
    scope: str = Query(default="day"),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    """Backwards-compatible alias for the itemized Daily Sales Report CSV."""
    return _stream_sales_ledger_csv(
        db=db,
        period="daily",
        report_date=report_date,
        report_week=None,
        report_month=None,
        report_year=None,
        scope=scope,
    )


@router.get("/orders/export-csv")
async def export_courier_dispatch_csv(
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    """Generates a downloadable CSV dispatch sheet formatted for Addis Ababa riders."""
    orders = db.scalars(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .order_by(Order.created_at.desc())
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Order Number",
            "Status",
            "Sub-City",
            "Woreda",
            "Landmark Description",
            "GPS Pin",
            "Recipient Name",
            "Recipient Phone",
            "Payment Method",
            "Payment Status",
            "COD Amount to Collect (ETB)",
            "Items Summary",
        ]
    )

    for o in orders:
        addr = o.delivery_address
        cod_to_collect = (
            f"{o.total_etb:.2f}" if o.payment_status == PaymentStatus.UNPAID else "0.00"
        )
        items_summary = "; ".join(f"{i.quantity}x {i.product.sku}" for i in o.items)
        writer.writerow(
            [
                o.order_number,
                o.status.value,
                addr.sub_city,
                addr.woreda,
                addr.landmark_description,
                addr.gps_coordinates or "",
                addr.recipient_name,
                addr.recipient_phone,
                o.payment_method.value,
                o.payment_status.value,
                cod_to_collect,
                items_summary,
            ]
        )

    output.seek(0)
    filename = f"lijnet_addis_dispatch_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/vat-csv")
async def export_mor_vat_report_csv(
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    """Generates an Ethiopian Ministry of Revenues (MoR) 15% VAT Sales Register CSV."""
    orders = db.scalars(
        select(Order)
        .options(selectinload(Order.delivery_address))
        .order_by(Order.created_at.desc())
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Fiscal Invoice No",
            "Order Number",
            "Issue Date (UTC)",
            "Seller TIN",
            "Customer Name",
            "Customer Phone",
            "Sub-City",
            "Order Status",
            "Payment Method",
            "Payment Status",
            "Taxable Base Excl. VAT (ETB)",
            "Output VAT 15% (ETB)",
            "Gross Total Incl. VAT (ETB)",
        ]
    )

    for o in orders:
        fiscal_no = f"FS-{o.created_at.year}-{o.id:06d}"
        excl_vat, vat_amt = _split_vat_inclusive(o.total_etb, settings.VAT_RATE)
        writer.writerow(
            [
                fiscal_no,
                o.order_number,
                o.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                settings.SELLER_TIN,
                o.delivery_address.recipient_name,
                o.delivery_address.recipient_phone,
                o.delivery_address.sub_city,
                o.status.value,
                o.payment_method.value,
                o.payment_status.value,
                f"{excl_vat:.2f}",
                f"{vat_amt:.2f}",
                f"{o.total_etb:.2f}",
            ]
        )

    output.seek(0)
    filename = f"lijnet_mor_15pct_vat_register_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/inventory-csv")
async def export_inventory_compliance_csv(
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    """Generates an Inventory Valuation & Proclamation No. 1205/2020 Origin Audit CSV."""
    products = db.scalars(select(Product).order_by(Product.sku.asc())).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "SKU",
            "Title (EN)",
            "Title (AM)",
            "Category",
            "Age Range",
            "Origin Type",
            "Safety Certified",
            "Hygiene Sealed",
            "Active",
            "Unit Price (ETB)",
            "Stock Count",
            "Stock Retail Value (ETB)",
        ]
    )

    for p in products:
        val = p.price_etb * p.stock_count
        writer.writerow(
            [
                p.sku,
                p.title_en,
                p.title_am,
                p.category.value,
                p.age_range.value,
                p.origin_type.value,
                "Yes" if p.safety_certified else "No",
                "Yes" if p.is_hygiene_sealed else "No",
                "Yes" if p.is_active else "No",
                f"{p.price_etb:.2f}",
                p.stock_count,
                f"{val:.2f}",
            ]
        )

    output.seek(0)
    filename = f"lijnet_inventory_1205_audit_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# PLATFORM SETTINGS ROUTES
# ---------------------------------------------------------------------------


@router.post("/settings/update")
async def update_general_settings(
    legal_name: str = Form(...),
    seller_tin: str = Form(...),
    seller_vat_reg_no: str = Form(...),
    seller_vat_reg_date: str = Form(...),
    seller_address: str = Form(...),
    free_delivery_threshold_etb: Decimal = Form(...),
    cod_max_limit_etb: Decimal = Form(...),
    statutory_return_days: int = Form(...),
    local_content_min_pct: float = Form(...),
    admin_user: User = Depends(require_admin_session),
):
    save_platform_settings(
        {
            "legal_name": legal_name.strip(),
            "seller_tin": seller_tin.strip(),
            "seller_vat_reg_no": seller_vat_reg_no.strip(),
            "seller_vat_reg_date": seller_vat_reg_date.strip(),
            "seller_address": seller_address.strip(),
            "free_delivery_threshold_etb": str(max(free_delivery_threshold_etb, Decimal("0.00"))),
            "cod_max_limit_etb": str(max(cod_max_limit_etb, Decimal("0.00"))),
            "statutory_return_days": max(statutory_return_days, 1),
            "local_content_min_pct": min(max(local_content_min_pct, 0.0), 100.0),
        }
    )
    return RedirectResponse(
        url="/admin?message=Business%2C+MoR+TIN%2FVAT+and+compliance+settings+saved.#settings",
        status_code=303,
    )


@router.post("/settings/subcity-fees")
async def update_subcity_delivery_fees(
    request: Request,
    admin_user: User = Depends(require_admin_session),
):
    form_data = await request.form()
    updated_fees: dict[str, str] = {}
    for sub_city in ADDIS_SUBCITY_FEES.keys():
        field_key = f"fee_{sub_city}"
        if field_key in form_data:
            try:
                fee_val = max(Decimal(str(form_data[field_key])), Decimal("0.00"))
                updated_fees[sub_city] = f"{fee_val:.2f}"
            except Exception:
                pass

    save_platform_settings({"subcity_fees": updated_fees})
    return RedirectResponse(
        url="/admin?message=Addis+Ababa+sub-city+delivery+fees+updated.#settings",
        status_code=303,
    )


@router.post("/settings/telegram-webhook")
async def register_telegram_webhook_from_settings(
    admin_user: User = Depends(require_admin_session),
):
    bot = TelegramBotClient()
    if not bot.is_configured:
        return RedirectResponse(
            url="/admin?message=Set+TELEGRAM_BOT_TOKEN+in+.env+before+registering+webhook.#settings",
            status_code=303,
        )
    webhook_url = f"{settings.BASE_URL.rstrip('/')}/telegram/webhook"
    await bot.set_webhook(webhook_url)
    return RedirectResponse(
        url="/admin?message=Telegram+webhook+registered+successfully.#settings",
        status_code=303,
    )
