import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ADDIS_SUBCITY_FEES, settings
from app.database import get_db
from app.models import (
    DeliveryAddress,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    Product,
)
from app.schemas import DeliveryAddressCreate
from app.services.i18n import build_cart_summary, get_lang, get_translations, templates
from app.services.payments import ChapaAdapter, initiate_payment
from app.services.telegram_bot import notify_order_event

router = APIRouter(prefix="/checkout", tags=["Checkout & Payments"])


@router.get("", response_class=HTMLResponse)
async def checkout_page(
    request: Request,
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    lang = get_lang(request)
    t = get_translations(lang)
    sub_city = request.session.get("sub_city", "Bole")
    cart = build_cart_summary(request, db, sub_city=sub_city)

    return templates.TemplateResponse(
        request=request,
        name="checkout.html",
        context={
            "lang": lang,
            "t": t,
            "cart": cart,
            "sub_cities": list(ADDIS_SUBCITY_FEES.keys()),
            "payment_methods": [pm.value for pm in PaymentMethod],
            "cod_limit": settings.COD_MAX_LIMIT_ETB,
            "error": error,
        },
    )


@router.post("/submit")
async def submit_checkout(
    request: Request,
    recipient_name: str = Form(...),
    recipient_phone: str = Form(...),
    customer_email: str = Form("parent@lijnet.et"),
    sub_city: str = Form(...),
    woreda: str = Form(...),
    landmark_description: str = Form(...),
    gps_coordinates: str = Form(""),
    payment_method: str = Form(...),
    db: Session = Depends(get_db),
):
    cart = build_cart_summary(request, db, sub_city=sub_city)
    if not cart["items"]:
        return RedirectResponse(url="/checkout?error=Your+cart+is+empty", status_code=303)

    # Validate Addis Ababa address & Ethiopian phone format via Pydantic v2
    try:
        validated_addr = DeliveryAddressCreate(
            recipient_name=recipient_name,
            sub_city=sub_city,
            woreda=woreda,
            landmark_description=landmark_description,
            gps_coordinates=gps_coordinates.strip() or None,
            recipient_phone=recipient_phone,
        )
        selected_method = PaymentMethod(payment_method)
    except (ValidationError, ValueError) as exc:
        msg = str(exc).splitlines()[0][:180]
        return RedirectResponse(url=f"/checkout?error={msg}", status_code=303)

    # Enforce Cash-on-Delivery ceiling (ETB 5,000)
    if selected_method == PaymentMethod.COD and cart["total_etb"] > settings.COD_MAX_LIMIT_ETB:
        return RedirectResponse(
            url=f"/checkout?error=Cash+on+Delivery+is+capped+at+ETB+{settings.COD_MAX_LIMIT_ETB:,.0f}.+Please+use+Chapa,+Telebirr,+or+CBE+Transfer.",
            status_code=303,
        )

    # Persist Delivery Address & Order atomically with stock locking
    address = DeliveryAddress(**validated_addr.model_dump())
    db.add(address)
    db.flush()

    now_year = datetime.now(timezone.utc).year
    short_code = uuid.uuid4().hex[:6].upper()
    order_number = f"LIJ-{now_year}-{short_code}"
    tx_ref = f"LIJ-TX-{uuid.uuid4().hex[:10].upper()}"

    order = Order(
        order_number=order_number,
        delivery_address_id=address.id,
        status=OrderStatus.CONFIRMED
        if selected_method == PaymentMethod.COD
        else OrderStatus.PENDING,
        payment_method=selected_method,
        payment_status=PaymentStatus.UNPAID,
        tx_ref=tx_ref,
        subtotal_etb=cart["subtotal_etb"],
        delivery_fee_etb=cart["delivery_fee_etb"],
        total_etb=cart["total_etb"],
    )
    db.add(order)
    db.flush()

    for entry in cart["items"]:
        prod: Product = db.scalar(
            select(Product).where(Product.id == entry["product_obj"].id).with_for_update()
        )
        if not prod or prod.stock_count < entry["quantity"]:
            db.rollback()
            return RedirectResponse(
                url="/checkout?error=Insufficient+stock+for+one+or+more+items",
                status_code=303,
            )
        prod.stock_count -= entry["quantity"]
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=prod.id,
                quantity=entry["quantity"],
                unit_price_etb=prod.price_etb,
                line_total_etb=entry["line_total_etb"],
            )
        )

    db.commit()
    await notify_order_event(db, order, "Order Received on Lijnet")

    # Clear cart and dispatch payment provider
    request.session["cart"] = {}
    payment_result = await initiate_payment(
        order=order,
        customer_email=customer_email,
        recipient_name=validated_addr.recipient_name,
        recipient_phone=validated_addr.recipient_phone,
    )

    if payment_result.instructions:
        request.session["last_payment_note"] = payment_result.instructions

    if payment_result.success and payment_result.checkout_url:
        return RedirectResponse(url=payment_result.checkout_url, status_code=303)

    return RedirectResponse(url=f"/orders?highlight={order.order_number}", status_code=303)


@router.get("/callback/chapa")
async def chapa_return_callback(
    tx_ref: str = Query(...),
    simulated: int = Query(0),
    db: Session = Depends(get_db),
):
    order = db.scalar(select(Order).where(Order.tx_ref == tx_ref))
    if not order:
        raise HTTPException(status_code=404, detail="Transaction reference not found.")

    if simulated == 1:
        is_paid = True
    else:
        is_paid, _ = await ChapaAdapter().verify_transaction(tx_ref)

    if is_paid:
        order.payment_status = PaymentStatus.PAID
        if order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CONFIRMED
        db.commit()
        await notify_order_event(db, order, "Payment Confirmed (Chapa)")

    return RedirectResponse(url=f"/orders?highlight={order.order_number}", status_code=303)


@router.post("/webhooks/chapa")
async def chapa_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("x-chapa-signature") or request.headers.get("Chapa-Signature")
    if not ChapaAdapter.verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid Chapa webhook signature.")

    payload = await request.json()
    tx_ref = payload.get("tx_ref")
    if not tx_ref:
        return JSONResponse({"status": "ignored"})

    order = db.scalar(select(Order).where(Order.tx_ref == tx_ref))
    if order and payload.get("status") == "success":
        order.payment_status = PaymentStatus.PAID
        if order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CONFIRMED
        db.commit()
        await notify_order_event(db, order, "Payment Confirmed (Chapa)")

    return JSONResponse({"status": "processed", "tx_ref": tx_ref})


@router.post("/webhooks/telebirr")
async def telebirr_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    tx_ref = payload.get("merch_order_id") or payload.get("outTradeNo")
    order = db.scalar(select(Order).where(Order.tx_ref == tx_ref)) if tx_ref else None
    if order and payload.get("trade_status") in ("Completed", "TRADE_SUCCESS"):
        order.payment_status = PaymentStatus.PAID
        if order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CONFIRMED
        db.commit()
    return JSONResponse({"code": 0, "msg": "success"})


@router.post("/webhooks/santimpay")
async def santimpay_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    tx_ref = payload.get("thirdPartyId") or payload.get("id")
    order = db.scalar(select(Order).where(Order.tx_ref == tx_ref)) if tx_ref else None
    if order and payload.get("Status") == "COMPLETED":
        order.payment_status = PaymentStatus.PAID
        if order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CONFIRMED
        db.commit()
    return JSONResponse({"status": "ok"})
