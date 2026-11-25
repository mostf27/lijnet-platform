import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
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
)
from app.services.auth import require_vendor_session
from app.services.i18n import get_lang, get_translations, templates
from app.services.invoices import _split_vat_inclusive

router = APIRouter(prefix="/seller", tags=["Product Owner / Seller Portal"])

LOCAL_THUMBNAILS = [
    ("/static/images/onesie.svg", "Ethiopian Cotton Apparel"),
    ("/static/images/starter-box.svg", "Newborn Gift & Starter Box"),
    ("/static/images/fidel-puzzle.svg", "Ge'ez Fidel Wooden Toy"),
    ("/static/images/bottle.svg", "Feeding Bottle & Care"),
    ("/static/images/diapers.svg", "Baby Diapers & Hygiene"),
    ("/static/images/wipes.svg", "Organic Baby Wipes"),
    ("/static/images/basket.svg", "Handwoven Nursery Basket"),
    ("/static/images/crib.svg", "Nursery Crib & Bedding"),
    ("/static/images/default-product.svg", "Lijnet Default Badge"),
]


@router.get("", response_class=HTMLResponse)
async def seller_portal_dashboard(
    request: Request,
    message: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
    vendor_user: User = Depends(require_vendor_session),
):
    lang = get_lang(request)
    t = get_translations(lang)

    my_products = db.scalars(
        select(Product)
        .where(Product.vendor_id == vendor_user.id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    my_product_ids = {p.id for p in my_products}

    # Fetch orders that contain at least one item owned by this Product Owner / Seller
    seller_orders_view: list[dict] = []
    gross_sales_etb = Decimal("0.00")
    paid_sales_etb = Decimal("0.00")
    total_units_sold = 0
    sold_by_product: dict[int, int] = {}

    if my_product_ids:
        matched_orders = db.scalars(
            select(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(OrderItem.product_id.in_(my_product_ids))
            .options(
                selectinload(Order.delivery_address),
                selectinload(Order.items).selectinload(OrderItem.product),
            )
            .distinct()
            .order_by(Order.created_at.desc())
        ).all()

        for order in matched_orders:
            vendor_items = [item for item in order.items if item.product_id in my_product_ids]
            if not vendor_items:
                continue

            order_vendor_subtotal = sum(
                (item.line_total_etb for item in vendor_items), Decimal("0.00")
            )
            order_vendor_qty = sum(item.quantity for item in vendor_items)

            if order.status not in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
                gross_sales_etb += order_vendor_subtotal
                total_units_sold += order_vendor_qty
                if order.payment_status == PaymentStatus.PAID:
                    paid_sales_etb += order_vendor_subtotal
                for item in vendor_items:
                    sold_by_product[item.product_id] = (
                        sold_by_product.get(item.product_id, 0) + item.quantity
                    )

            seller_orders_view.append(
                {
                    "id": order.id,
                    "order_number": order.order_number,
                    "created_at": order.created_at,
                    "status": order.status.value,
                    "payment_method": order.payment_method.value,
                    "payment_status": order.payment_status.value,
                    "recipient_name": (
                        order.delivery_address.recipient_name
                        if order.delivery_address
                        else "Customer"
                    ),
                    "sub_city": (
                        order.delivery_address.sub_city if order.delivery_address else "Addis Ababa"
                    ),
                    "woreda": (order.delivery_address.woreda if order.delivery_address else ""),
                    "items": vendor_items,
                    "vendor_subtotal_etb": order_vendor_subtotal,
                    "vendor_qty": order_vendor_qty,
                }
            )

    net_excl_vat_etb, vat_15_etb = _split_vat_inclusive(gross_sales_etb, settings.VAT_RATE)
    local_count = sum(
        1 for p in my_products if p.is_active and p.origin_type == OriginType.LOCAL_ETHIOPIAN
    )
    active_count = sum(1 for p in my_products if p.is_active)
    local_share_pct = round((local_count / active_count) * 100, 1) if active_count else 0.0

    kpis = {
        "total_products": len(my_products),
        "active_products": active_count,
        "local_share_pct": local_share_pct,
        "total_units_sold": total_units_sold,
        "total_orders": len(seller_orders_view),
        "gross_sales_etb": gross_sales_etb,
        "paid_sales_etb": paid_sales_etb,
        "net_excl_vat_etb": net_excl_vat_etb,
        "vat_15_etb": vat_15_etb,
    }

    return templates.TemplateResponse(
        request=request,
        name="seller.html",
        context={
            "lang": lang,
            "t": t,
            "vendor_user": vendor_user,
            "kpis": kpis,
            "my_products": my_products,
            "sold_by_product": sold_by_product,
            "seller_orders": seller_orders_view,
            "categories": [c.value for c in ProductCategory],
            "age_ranges": [a.value for a in AgeRange],
            "origin_types": [o.value for o in OriginType],
            "thumbnails": LOCAL_THUMBNAILS,
            "message": message,
            "error": error,
        },
    )


@router.post("/products/create")
async def seller_create_product(
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
    thumbnail_preset: str = Form("/static/images/default-product.svg"),
    image_url: str = Form(""),
    safety_certified: bool = Form(True),
    is_hygiene_sealed: bool = Form(False),
    is_starter_bundle: bool = Form(False),
    db: Session = Depends(get_db),
    vendor_user: User = Depends(require_vendor_session),
):
    clean_sku = sku.strip().upper()
    existing_sku = db.scalar(select(Product).where(Product.sku == clean_sku))
    if existing_sku:
        return RedirectResponse(
            url=f"/seller?error=SKU+{clean_sku}+already+exists.+Please+choose+a+unique+SKU.#add-product",
            status_code=303,
        )

    if price_etb <= Decimal("0.00"):
        return RedirectResponse(
            url="/seller?error=Unit+price+must+be+greater+than+0.00+ETB.#add-product",
            status_code=303,
        )

    chosen_image = (
        image_url.strip() or thumbnail_preset.strip() or "/static/images/default-product.svg"
    )

    product = Product(
        sku=clean_sku,
        title_en=title_en.strip(),
        title_am=title_am.strip(),
        description_en=description_en.strip(),
        description_am=description_am.strip(),
        category=ProductCategory(category),
        age_range=AgeRange(age_range),
        origin_type=OriginType(origin_type),
        price_etb=price_etb,
        stock_count=max(stock_count, 0),
        vendor_id=vendor_user.id,
        image_url=chosen_image,
        safety_certified=safety_certified,
        is_hygiene_sealed=is_hygiene_sealed,
        is_starter_bundle=is_starter_bundle,
        is_active=True,
    )
    db.add(product)
    db.commit()

    return RedirectResponse(
        url=f"/seller?message=Listed+{product.title_en}+({product.sku})+on+Lijnet.#my-products",
        status_code=303,
    )


@router.post("/products/{product_id}/update")
async def seller_update_own_product(
    product_id: int,
    title_en: str = Form(...),
    title_am: str = Form(...),
    price_etb: Decimal = Form(...),
    stock_count: int = Form(...),
    is_active: str = Form("true"),
    db: Session = Depends(get_db),
    vendor_user: User = Depends(require_vendor_session),
):
    product = db.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.vendor_id == vendor_user.id,
        )
    )
    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found or you do not own this listing.",
        )

    product.title_en = title_en.strip()
    product.title_am = title_am.strip()
    product.price_etb = max(price_etb, Decimal("1.00"))
    product.stock_count = max(stock_count, 0)
    product.is_active = is_active.lower() == "true"
    db.commit()

    return RedirectResponse(
        url=f"/seller?message=Updated+{product.sku}+listing+and+stock.#my-products",
        status_code=303,
    )


@router.get("/orders/export-csv")
async def export_seller_orders_csv(
    db: Session = Depends(get_db),
    vendor_user: User = Depends(require_vendor_session),
):
    """Exports a CSV fulfillment report containing only this Product Owner's sold items."""
    my_products = db.scalars(select(Product).where(Product.vendor_id == vendor_user.id)).all()
    my_product_ids = {p.id for p in my_products}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Order Number",
            "Order Date (UTC)",
            "Order Status",
            "Payment Method",
            "Payment Status",
            "Addis Sub-City",
            "Recipient Name",
            "SKU",
            "Product Title (EN)",
            "Product Title (AM)",
            "Quantity",
            "Unit Price (ETB)",
            "Line Total Incl. VAT (ETB)",
            "Net Base Excl. 15% VAT (ETB)",
        ]
    )

    if my_product_ids:
        matched_orders = db.scalars(
            select(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(OrderItem.product_id.in_(my_product_ids))
            .options(
                selectinload(Order.delivery_address),
                selectinload(Order.items).selectinload(OrderItem.product),
            )
            .distinct()
            .order_by(Order.created_at.desc())
        ).all()

        for order in matched_orders:
            for item in order.items:
                if item.product_id not in my_product_ids:
                    continue
                net_excl, _ = _split_vat_inclusive(item.line_total_etb, settings.VAT_RATE)
                writer.writerow(
                    [
                        order.order_number,
                        order.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        order.status.value,
                        order.payment_method.value,
                        order.payment_status.value,
                        order.delivery_address.sub_city if order.delivery_address else "",
                        order.delivery_address.recipient_name if order.delivery_address else "",
                        item.product.sku,
                        item.product.title_en,
                        item.product.title_am,
                        item.quantity,
                        f"{item.unit_price_etb:.2f}",
                        f"{item.line_total_etb:.2f}",
                        f"{net_excl:.2f}",
                    ]
                )

    output.seek(0)
    filename = f"lijnet_seller_orders_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
