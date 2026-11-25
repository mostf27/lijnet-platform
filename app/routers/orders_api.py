import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    DeliveryAddress,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    Product,
    User,
)
from app.schemas import DeliveryAddressCreate, ReturnRequestSchema
from app.services.auth import get_current_api_user
from app.services.compliance import evaluate_return_eligibility
from app.services.i18n import calculate_delivery_fee
from app.services.invoices import generate_vat_invoice_pdf
from app.services.payments import initiate_payment
from app.services.telegram_bot import notify_order_event

router = APIRouter(prefix="/api/v1/orders", tags=["Mobile API Orders"])


class MobileOrderItemIn(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=50)


class MobileOrderCreate(BaseModel):
    items: list[MobileOrderItemIn] = Field(..., min_length=1)
    address: DeliveryAddressCreate
    payment_method: PaymentMethod


class MobileAddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recipient_name: str
    sub_city: str
    woreda: str
    landmark_description: str
    gps_coordinates: str | None
    recipient_phone: str


class MobileOrderItemOut(BaseModel):
    id: int
    product_id: int
    sku: str
    title_en: str
    title_am: str
    quantity: int
    unit_price_etb: Decimal
    line_total_etb: Decimal
    is_hygiene_sealed: bool


class MobileOrderDetailOut(BaseModel):
    id: int
    order_number: str
    status: OrderStatus
    payment_method: PaymentMethod
    payment_status: PaymentStatus
    tx_ref: str
    subtotal_etb: Decimal
    delivery_fee_etb: Decimal
    total_etb: Decimal
    delivered_at: datetime | None
    return_reason: str | None
    return_requested_at: datetime | None
    created_at: datetime
    return_eligible: bool
    return_note: str
    delivery_address: MobileAddressOut
    items: list[MobileOrderItemOut]
    payment_checkout_url: str | None = None
    payment_instructions: str | None = None


def _serialize_mobile_order(
    order: Order,
    checkout_url: str | None = None,
    instructions: str | None = None,
) -> MobileOrderDetailOut:
    eligible, note = evaluate_return_eligibility(order, hygiene_seal_broken=False)
    items_out = [
        MobileOrderItemOut(
            id=item.id,
            product_id=item.product_id,
            sku=item.product.sku,
            title_en=item.product.title_en,
            title_am=item.product.title_am,
            quantity=item.quantity,
            unit_price_etb=item.unit_price_etb,
            line_total_etb=item.line_total_etb,
            is_hygiene_sealed=item.product.is_hygiene_sealed,
        )
        for item in order.items
    ]
    return MobileOrderDetailOut(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        payment_method=order.payment_method,
        payment_status=order.payment_status,
        tx_ref=order.tx_ref,
        subtotal_etb=order.subtotal_etb,
        delivery_fee_etb=order.delivery_fee_etb,
        total_etb=order.total_etb,
        delivered_at=order.delivered_at,
        return_reason=order.return_reason,
        return_requested_at=order.return_requested_at,
        created_at=order.created_at,
        return_eligible=eligible,
        return_note=note,
        delivery_address=MobileAddressOut.model_validate(order.delivery_address),
        items=items_out,
        payment_checkout_url=checkout_url,
        payment_instructions=instructions,
    )


@router.post("", response_model=MobileOrderDetailOut, status_code=status.HTTP_201_CREATED)
async def create_mobile_order(
    payload: MobileOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user),
):
    """Creates an Addis Ababa order from the mobile app with atomic stock locking and payment init."""
    requested_qty: dict[int, int] = {}
    for item in payload.items:
        requested_qty[item.product_id] = requested_qty.get(item.product_id, 0) + item.quantity

    locked_products = db.scalars(
        select(Product)
        .where(Product.id.in_(requested_qty.keys()), Product.is_active.is_(True))
        .with_for_update()
    ).all()
    product_map = {p.id: p for p in locked_products}

    if len(product_map) != len(requested_qty):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more requested products do not exist or are inactive.",
        )

    subtotal_etb = Decimal("0.00")
    line_records: list[tuple[Product, int, Decimal]] = []

    for pid, qty in requested_qty.items():
        product = product_map[pid]
        if product.stock_count < qty:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Insufficient stock for '{product.title_en}' (available: {product.stock_count}).",
            )
        line_total = product.price_etb * qty
        subtotal_etb += line_total
        line_records.append((product, qty, line_total))

    delivery_fee_etb = calculate_delivery_fee(subtotal_etb, payload.address.sub_city)
    total_etb = subtotal_etb + delivery_fee_etb

    if payload.payment_method == PaymentMethod.COD and total_etb > settings.COD_MAX_LIMIT_ETB:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cash on Delivery is capped at {settings.COD_MAX_LIMIT_ETB:,.2f} ETB. "
                "Please select Chapa, Telebirr, SantimPay, or CBE Transfer."
            ),
        )

    address = DeliveryAddress(
        user_id=current_user.id,
        **payload.address.model_dump(),
    )
    db.add(address)
    db.flush()

    now_year = datetime.now(timezone.utc).year
    order_number = f"LIJ-{now_year}-{uuid.uuid4().hex[:6].upper()}"
    tx_ref = f"LIJ-TX-{uuid.uuid4().hex[:10].upper()}"

    order = Order(
        order_number=order_number,
        user_id=current_user.id,
        delivery_address_id=address.id,
        status=(
            OrderStatus.CONFIRMED
            if payload.payment_method == PaymentMethod.COD
            else OrderStatus.PENDING
        ),
        payment_method=payload.payment_method,
        payment_status=PaymentStatus.UNPAID,
        tx_ref=tx_ref,
        subtotal_etb=subtotal_etb,
        delivery_fee_etb=delivery_fee_etb,
        total_etb=total_etb,
    )
    db.add(order)
    db.flush()

    for product, qty, line_total in line_records:
        product.stock_count -= qty
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=qty,
                unit_price_etb=product.price_etb,
                line_total_etb=line_total,
            )
        )

    db.commit()

    created_order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .where(Order.id == order.id)
    )

    await notify_order_event(db, created_order, "Mobile Order Confirmed")

    payment_result = await initiate_payment(
        order=created_order,
        customer_email=current_user.email,
        recipient_name=address.recipient_name,
        recipient_phone=address.recipient_phone,
    )

    return _serialize_mobile_order(
        created_order,
        checkout_url=payment_result.checkout_url,
        instructions=payment_result.instructions,
    )


@router.get("", response_model=list[MobileOrderDetailOut])
async def list_mobile_user_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user),
):
    """Returns all orders belonging to the authenticated mobile customer."""
    orders = db.scalars(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .where(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
    ).all()

    return [_serialize_mobile_order(o) for o in orders]


@router.get("/{order_id}", response_model=MobileOrderDetailOut)
async def get_mobile_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user),
):
    """Returns full tracking details and 7-day return eligibility for a single user order."""
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .where(Order.id == order_id, Order.user_id == current_user.id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )

    return _serialize_mobile_order(order)


@router.post("/{order_id}/return", response_model=MobileOrderDetailOut)
async def request_mobile_order_return(
    order_id: int,
    payload: ReturnRequestSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user),
):
    """Submits a statutory 7-day return/exchange request under Proclamation No. 1205/2020."""
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .where(Order.id == order_id, Order.user_id == current_user.id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )

    eligible, msg = evaluate_return_eligibility(
        order,
        hygiene_seal_broken=payload.hygiene_seal_broken,
    )
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg,
        )

    order.status = OrderStatus.RETURN_REQUESTED
    order.return_reason = payload.reason.strip()
    order.return_requested_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)

    return _serialize_mobile_order(order)


@router.get("/{order_id}/invoice.pdf")
async def download_mobile_order_vat_invoice(
    order_id: int,
    buyer_tin: str | None = Query(default=None, max_length=15),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user),
):
    """Streams a 15% VAT receipt/invoice PDF for an authenticated mobile customer's order."""
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.delivery_address),
            selectinload(Order.items).selectinload(OrderItem.product),
        )
        .where(Order.id == order_id, Order.user_id == current_user.id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )

    pdf_bytes = generate_vat_invoice_pdf(order, buyer_tin=buyer_tin)
    filename = f"Lijnet_VAT_Invoice_{order.order_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
