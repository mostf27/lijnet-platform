import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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
from app.services.telegram_bot import notify_order_event

router = APIRouter(prefix="/admin", tags=["Admin & Courier Dispatch"])


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
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    lang = get_lang(request)
    t = get_translations(lang)
    cart = build_cart_summary(request, db, sub_city=request.session.get("sub_city", "Bole"))
    compliance = calculate_local_content_compliance(db)

    products = db.scalars(select(Product).order_by(Product.created_at.desc())).all()
    orders = db.scalars(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .order_by(Order.created_at.desc())
        .limit(50)
    ).all()
    vendors = db.scalars(select(User).where(User.role.in_([UserRole.VENDOR, UserRole.ADMIN]))).all()

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "lang": lang,
            "t": t,
            "cart": cart,
            "admin_user": admin_user,
            "compliance": compliance,
            "products": products,
            "orders": orders,
            "vendors": vendors,
            "categories": [c.value for c in ProductCategory],
            "age_ranges": [a.value for a in AgeRange],
            "origin_types": [o.value for o in OriginType],
            "order_statuses": [s.value for s in OrderStatus],
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
        image_url=image_url.strip()
        or "https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?w=600&q=80",
        safety_certified=safety_certified,
        is_hygiene_sealed=is_hygiene_sealed,
        is_starter_bundle=is_starter_bundle,
    )
    db.add(product)
    db.commit()
    return RedirectResponse(
        url="/admin?message=Bilingual+product+created+successfully.", status_code=303
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
    return RedirectResponse(url="/admin?message=Inventory+updated.", status_code=303)


@router.post("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_session),
):
    order = db.scalar(select(Order).where(Order.id == order_id))
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
        url=f"/admin?message=Order+{order.order_number}+updated+to+{new_status.value}.",
        status_code=303,
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
