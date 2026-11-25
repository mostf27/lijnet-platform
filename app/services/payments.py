import hashlib
import hmac
from typing import Any

import httpx

from app.config import settings
from app.models import Order, PaymentMethod


class PaymentInitResult:
    def __init__(
        self,
        success: bool,
        provider: str,
        tx_ref: str,
        checkout_url: str | None = None,
        instructions: str | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.provider = provider
        self.tx_ref = tx_ref
        self.checkout_url = checkout_url
        self.instructions = instructions
        self.raw_response = raw_response or {}


class ChapaAdapter:
    """Async Chapa Payment Gateway adapter (Telebirr, CBE Birr, Cards)."""

    async def initialize_transaction(
        self,
        order: Order,
        customer_email: str,
        recipient_name: str,
        recipient_phone: str,
    ) -> PaymentInitResult:
        name_parts = recipient_name.strip().split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else "Parent"

        payload = {
            "amount": f"{order.total_etb:.2f}",
            "currency": "ETB",
            "email": customer_email,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": recipient_phone.replace("+251", "0"),
            "tx_ref": order.tx_ref,
            "callback_url": f"{settings.BASE_URL}/checkout/webhooks/chapa",
            "return_url": f"{settings.BASE_URL}/checkout/callback/chapa?tx_ref={order.tx_ref}",
            "customization": {
                "title": "Lijnet Kids Store",
                "description": f"Order {order.order_number}",
            },
        }

        # Sandbox/local dev fallback when no live Chapa secret key is configured
        if settings.CHAPA_SECRET_KEY.startswith(
            "CHASECK_TEST-replace"
        ) or settings.CHAPA_SECRET_KEY.endswith("placeholder"):
            return PaymentInitResult(
                success=True,
                provider="CHAPA",
                tx_ref=order.tx_ref,
                checkout_url=f"{settings.BASE_URL}/checkout/callback/chapa?tx_ref={order.tx_ref}&simulated=1",
                instructions="Sandbox mode: simulated Chapa checkout link generated.",
            )

        headers = {
            "Authorization": f"Bearer {settings.CHAPA_SECRET_KEY}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{settings.CHAPA_BASE_URL}/transaction/initialize",
                json=payload,
                headers=headers,
            )
            data = resp.json()

        if resp.status_code == 200 and data.get("status") == "success":
            return PaymentInitResult(
                success=True,
                provider="CHAPA",
                tx_ref=order.tx_ref,
                checkout_url=data["data"]["checkout_url"],
                raw_response=data,
            )

        return PaymentInitResult(
            success=False,
            provider="CHAPA",
            tx_ref=order.tx_ref,
            instructions=str(data.get("message", "Chapa initialization failed.")),
            raw_response=data,
        )

    async def verify_transaction(self, tx_ref: str) -> tuple[bool, dict[str, Any]]:
        if settings.CHAPA_SECRET_KEY.startswith(
            "CHASECK_TEST-replace"
        ) or settings.CHAPA_SECRET_KEY.endswith("placeholder"):
            return True, {"status": "success", "tx_ref": tx_ref, "simulated": True}

        headers = {"Authorization": f"Bearer {settings.CHAPA_SECRET_KEY}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{settings.CHAPA_BASE_URL}/transaction/verify/{tx_ref}",
                headers=headers,
            )
            data = resp.json()

        is_paid = (
            resp.status_code == 200
            and data.get("status") == "success"
            and data.get("data", {}).get("status") == "success"
        )
        return is_paid, data

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
        if not signature_header:
            return False
        expected = hmac.new(
            settings.CHAPA_WEBHOOK_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header.strip())


class TelebirrAdapter:
    """Async Telebirr Direct Merchant H5/Web adapter."""

    async def initialize_transaction(self, order: Order, recipient_phone: str) -> PaymentInitResult:
        if not settings.TELEBIRR_APP_ID or settings.TELEBIRR_APP_ID.startswith("replace"):
            return PaymentInitResult(
                success=True,
                provider="TELEBIRR",
                tx_ref=order.tx_ref,
                checkout_url=f"{settings.BASE_URL}/orders?highlight={order.order_number}",
                instructions=(
                    f"Telebirr USSD/App Pay: Send {order.total_etb:,.2f} ETB to Lijnet Merchant "
                    f"ShortCode 882211 with Reference {order.order_number}."
                ),
            )

        payload = {
            "appid": settings.TELEBIRR_APP_ID,
            "merch_code": settings.TELEBIRR_SHORT_CODE,
            "merch_order_id": order.tx_ref,
            "total_amount": f"{order.total_etb:.2f}",
            "trans_currency": "ETB",
            "notify_url": f"{settings.BASE_URL}/checkout/webhooks/telebirr",
            "redirect_url": f"{settings.BASE_URL}/orders?highlight={order.order_number}",
            "title": f"Lijnet Order {order.order_number}",
            "msisdn": recipient_phone.replace("+", ""),
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{settings.TELEBIRR_BASE_URL}/payment/v1/merchant/preOrder",
                json=payload,
                headers={"X-APP-Key": settings.TELEBIRR_APP_KEY},
            )
            data = resp.json()

        return PaymentInitResult(
            success=(resp.status_code == 200),
            provider="TELEBIRR",
            tx_ref=order.tx_ref,
            checkout_url=data.get("biz_content", {}).get("toPayUrl"),
            raw_response=data,
        )


class SantimPayAdapter:
    """Async SantimPay Gateway adapter."""

    async def initialize_transaction(self, order: Order, recipient_phone: str) -> PaymentInitResult:
        if not settings.SANTIMPAY_MERCHANT_ID or settings.SANTIMPAY_MERCHANT_ID.startswith(
            "replace"
        ):
            return PaymentInitResult(
                success=True,
                provider="SANTIMPAY",
                tx_ref=order.tx_ref,
                checkout_url=f"{settings.BASE_URL}/orders?highlight={order.order_number}",
                instructions=f"SantimPay sandbox initialized for {order.total_etb:,.2f} ETB (Ref: {order.tx_ref}).",
            )

        payload = {
            "id": order.tx_ref,
            "amount": float(order.total_etb),
            "reason": f"Lijnet Order {order.order_number}",
            "merchantId": settings.SANTIMPAY_MERCHANT_ID,
            "phoneNumber": recipient_phone,
            "successRedirectUrl": f"{settings.BASE_URL}/orders?highlight={order.order_number}",
            "failureRedirectUrl": f"{settings.BASE_URL}/checkout?error=payment_failed",
            "notifyUrl": f"{settings.BASE_URL}/checkout/webhooks/santimpay",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{settings.SANTIMPAY_BASE_URL}/initiate-payment", json=payload
            )
            data = resp.json()

        return PaymentInitResult(
            success=(resp.status_code == 200 and "url" in data),
            provider="SANTIMPAY",
            tx_ref=order.tx_ref,
            checkout_url=data.get("url"),
            raw_response=data,
        )


async def initiate_payment(
    order: Order,
    customer_email: str,
    recipient_name: str,
    recipient_phone: str,
) -> PaymentInitResult:
    if order.payment_method == PaymentMethod.CHAPA:
        return await ChapaAdapter().initialize_transaction(
            order, customer_email, recipient_name, recipient_phone
        )

    if order.payment_method == PaymentMethod.TELEBIRR:
        return await TelebirrAdapter().initialize_transaction(order, recipient_phone)

    if order.payment_method == PaymentMethod.SANTIMPAY:
        return await SantimPayAdapter().initialize_transaction(order, recipient_phone)

    if order.payment_method == PaymentMethod.CBE_TRANSFER:
        return PaymentInitResult(
            success=True,
            provider="CBE_TRANSFER",
            tx_ref=order.tx_ref,
            checkout_url=f"{settings.BASE_URL}/orders?highlight={order.order_number}",
            instructions=(
                f"Transfer {order.total_etb:,.2f} ETB to CBE Account 1000-4589-22101 "
                f"(Lijnet Digital Commerce PLC) and quote Reference: {order.order_number}."
            ),
        )

    # Cash on Delivery (COD)
    return PaymentInitResult(
        success=True,
        provider="COD",
        tx_ref=order.tx_ref,
        checkout_url=f"{settings.BASE_URL}/orders?highlight={order.order_number}",
        instructions=(
            f"Cash on Delivery confirmed for {order.total_etb:,.2f} ETB. "
            "Our Addis Ababa rider will call 30 minutes before arrival."
        ),
    )
