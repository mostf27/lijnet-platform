from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Order, OrderItem, OrderStatus
from app.services.compliance import evaluate_return_eligibility
from app.services.i18n import build_cart_summary, get_lang, get_translations, templates
from app.services.invoices import generate_vat_invoice_pdf

router = APIRouter(prefix="/orders", tags=["Orders & Statutory Returns"])


@router.get("", response_class=HTMLResponse)
async def list_orders(
    request: Request,
    highlight: str | None = Query(default=None),
    message: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    lang = get_lang(request)
    t = get_translations(lang)
    cart = build_cart_summary(request, db, sub_city=request.session.get("sub_city", "Bole"))
    payment_note = request.session.pop("last_payment_note", None)

    orders = db.scalars(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .order_by(Order.created_at.desc())
        .limit(30)
    ).all()

    enriched_orders = []
    for o in orders:
        eligible, eligibility_msg = evaluate_return_eligibility(o, hygiene_seal_broken=False)
        enriched_orders.append(
            {
                "order": o,
                "return_eligible": eligible,
                "return_note": eligibility_msg,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={
            "lang": lang,
            "t": t,
            "cart": cart,
            "orders": enriched_orders,
            "highlight": highlight,
            "payment_note": payment_note,
            "message": message,
            "error": error,
        },
    )


@router.post("/{order_id}/return")
async def request_order_return(
    order_id: int,
    reason: str = Form(...),
    hygiene_seal_broken: bool = Form(False),
    db: Session = Depends(get_db),
):
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    eligible, msg = evaluate_return_eligibility(order, hygiene_seal_broken=hygiene_seal_broken)
    if not eligible:
        return RedirectResponse(url=f"/orders?error={msg}", status_code=303)

    order.status = OrderStatus.RETURN_REQUESTED
    order.return_reason = reason.strip()
    order.return_requested_at = datetime.now(timezone.utc)
    db.commit()

    return RedirectResponse(
        url=f"/orders?highlight={order.order_number}&message=Return+request+approved+under+7-day+Proclamation+1205/2020+window.",
        status_code=303,
    )


@router.get("/{order_id}/invoice.pdf")
async def download_order_vat_invoice_pdf(
    order_id: int,
    buyer_tin: str | None = Query(default=None, max_length=15),
    db: Session = Depends(get_db),
):
    """Generates and streams a downloadable 15% VAT receipt/invoice PDF."""
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    pdf_bytes = generate_vat_invoice_pdf(order, buyer_tin=buyer_tin)
    filename = f"Lijnet_VAT_Invoice_{order.order_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
