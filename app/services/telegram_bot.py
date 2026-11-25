import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import SessionLocal
from app.models import (
    DeliveryAddress,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    Product,
    ProductCategory,
    User,
    UserRole,
)
from app.schemas import validate_ethiopian_phone
from app.services.auth import hash_password
from app.services.i18n import calculate_delivery_fee
from app.services.payments import initiate_payment

logger = logging.getLogger("lijnet.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"

BOT_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "welcome": (
            "👋 *Welcome to Lijnet (ልጅነት)!*\n"
            "Safe, authentic baby care & children's essentials delivered across Addis Ababa.\n\n"
            "• Tap *📱 Link Phone Number* to connect your Lijnet orders\n"
            "• Tap *📍 Share Delivery GPS Pin* so our rider finds your gate fast\n"
            "• Tap *🧷 1-Click Diaper/Wipes Reorder* for instant restocking"
        ),
        "btn_link_phone": "📱 Link Phone Number",
        "btn_share_gps": "📍 Share Delivery GPS Pin",
        "btn_reorder": "🧷 1-Click Diaper/Wipes Reorder",
        "btn_orders": "📦 My Orders",
        "btn_lang": "🌐 አማርኛ / English",
        "not_linked": (
            "⚠️ Your Telegram account is not linked to a Lijnet phone number yet.\n"
            "Tap *📱 Link Phone Number* below or send `/link 0911223344`."
        ),
        "linked_ok": (
            "✅ *Account Linked!*\n"
            "Parent: *{name}*\n"
            "Phone: `{phone}`\n\n"
            "You will now receive instant order updates here and can reorder diapers/wipes in 1 tap."
        ),
        "gps_saved": (
            "📍 *Delivery GPS Pin Saved!*\n"
            "Coordinates: `{coords}`\n"
            "Map: https://maps.google.com/?q={coords}\n"
            "{order_note}"
        ),
        "reorder_header": (
            "🧷 *1-Click Diaper & Wipes Restock*\n"
            "Delivering to: *{sub_city} (Woreda {woreda})*\n"
            "Landmark: _{landmark}_\n\n"
            "Tap an item below to place an instant reorder:"
        ),
        "no_address": (
            "⚠️ We don't have an Addis Ababa delivery landmark on file for your account yet.\n"
            "Place your first order at https://lijnet.et/checkout (or via the Lijnet mobile app), "
            "then use 1-click Telegram reorder anytime!"
        ),
    },
    "am": {
        "welcome": (
            "👋 *እንኳን ወደ ልጅነት (Lijnet) በደህና መጡ!*\n"
            "ትክክለኛነታቸው የተረጋገጡ የሕፃናት እንክብካቤ እና የልጆች እቃዎች በአዲስ አበባ በርዎ ድረስ።\n\n"
            "• ትዕዛዞችዎን ለማገናኘት *📱 Link Phone Number* ይጫኑ\n"
            "• ለአድራሻ ጂፒኤስ ለመላክ *📍 Share Delivery GPS Pin* ይጫኑ\n"
            "• ዳይፐር እና ዋይፕስ በፍጥነት ለማዘዝ *🧷 1-Click Diaper/Wipes Reorder* ይጫኑ"
        ),
        "btn_link_phone": "📱 Link Phone Number",
        "btn_share_gps": "📍 Share Delivery GPS Pin",
        "btn_reorder": "🧷 1-Click Diaper/Wipes Reorder",
        "btn_orders": "📦 My Orders",
        "btn_lang": "🌐 አማርኛ / English",
        "not_linked": (
            "⚠️ የቴሌግራም መለያዎ ከስልክ ቁጥርዎ ጋር አልተገናኘም።\n"
            "ከታች *📱 Link Phone Number* ይጫኑ ወይም `/link 0911223344` ብለው ይላኩ።"
        ),
        "linked_ok": (
            "✅ *መለያዎ በተሳካ ሁኔታ ተገናኝቷል!*\n"
            "ስም: *{name}*\n"
            "ስልክ: `{phone}`\n\n"
            "ከአሁን ጀምሮ የትዕዛዝ መረጃዎችን እዚህ ይደርስዎታል።"
        ),
        "gps_saved": (
            "📍 *የመላኪያ ጂፒኤስ (GPS) ተመዝግቧል!*\n"
            "መጋጠሚያ: `{coords}`\n"
            "ካርታ: https://maps.google.com/?q={coords}\n"
            "{order_note}"
        ),
        "reorder_header": (
            "🧷 *ፈጣን የዳይፐር እና ዋይፕስ ትዕዛዝ*\n"
            "መድረሻ: *{sub_city} (ወረዳ {woreda})*\n"
            "ልዩ ምልክት: _{landmark}_\n\n"
            "ለማዘዝ ከታች ያለውን ምርት ይጫኑ:"
        ),
        "no_address": (
            "⚠️ እስካሁን የተመዘገበ የአዲስ አበባ መላኪያ አድራሻ የለዎትም።\n"
            "የመጀመሪያ ትዕዛዝዎን በ https://lijnet.et/checkout ይፈጽሙ።"
        ),
    },
}


def build_main_keyboard(lang: str = "en") -> dict[str, Any]:
    t = BOT_TEXT.get(lang, BOT_TEXT["en"])
    return {
        "keyboard": [
            [{"text": t["btn_reorder"]}],
            [
                {"text": t["btn_share_gps"], "request_location": True},
                {"text": t["btn_link_phone"], "request_contact": True},
            ],
            [
                {"text": t["btn_orders"]},
                {"text": t["btn_lang"]},
            ],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


class TelegramBotClient:
    """Async HTTPX wrapper for the Telegram Bot API."""

    def __init__(self, token: str | None = None) -> None:
        self.token = (token if token is not None else settings.TELEGRAM_BOT_TOKEN).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.token and not self.token.startswith("paste-your"))

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.token}/{method}"

    async def call_api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured:
            logger.info("Telegram sandbox mode (no token set) -> %s: %s", method, payload)
            return {"ok": True, "simulated": True}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self._url(method), json=payload)
            data = resp.json()
            if not data.get("ok"):
                logger.warning("Telegram API %s error: %s", method, data)
            return data

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.call_api("sendMessage", payload)

    async def answer_callback_query(
        self, callback_query_id: str, text: str = "", show_alert: bool = False
    ) -> dict[str, Any]:
        return await self.call_api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            },
        )

    async def set_webhook(self, webhook_url: str) -> dict[str, Any]:
        return await self.call_api(
            "setWebhook",
            {
                "url": webhook_url,
                "secret_token": settings.TELEGRAM_WEBHOOK_SECRET,
                "allowed_updates": ["message", "callback_query"],
            },
        )


def _find_or_link_user_by_phone(
    db: Session,
    chat_id: str,
    raw_phone: str,
    telegram_first_name: str = "Parent",
) -> tuple[User | None, str | None]:
    """Normalizes an Ethiopian mobile number and binds telegram_chat_id to the User record."""
    try:
        normalized_phone = validate_ethiopian_phone(raw_phone)
    except ValueError as exc:
        return None, str(exc)

    # Clear this chat_id from any previous user record if re-linking
    existing_chat_user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
    if existing_chat_user and existing_chat_user.phone_number != normalized_phone:
        existing_chat_user.telegram_chat_id = None

    user = db.scalar(select(User).where(User.phone_number == normalized_phone))
    if not user:
        # Check if they placed a guest order with this phone number
        guest_addr = db.scalar(
            select(DeliveryAddress)
            .where(DeliveryAddress.recipient_phone == normalized_phone)
            .order_by(desc(DeliveryAddress.created_at))
        )
        full_name = guest_addr.recipient_name if guest_addr else telegram_first_name
        digits = normalized_phone.replace("+", "")
        user = User(
            full_name=full_name,
            email=f"tg.{digits}@lijnet.et",
            phone_number=normalized_phone,
            password_hash=hash_password(uuid.uuid4().hex),
            role=UserRole.CUSTOMER,
            is_active=True,
            is_verified=True,
            telegram_chat_id=chat_id,
        )
        db.add(user)
        db.flush()

        # Attach any prior guest orders/addresses with this phone number
        if guest_addr and guest_addr.user_id is None:
            guest_addr.user_id = user.id
        guest_orders = db.scalars(
            select(Order)
            .join(DeliveryAddress)
            .where(
                DeliveryAddress.recipient_phone == normalized_phone,
                Order.user_id.is_(None),
            )
        ).all()
        for o in guest_orders:
            o.user_id = user.id
    else:
        user.telegram_chat_id = chat_id

    db.commit()
    db.refresh(user)
    return user, None


def _get_latest_user_address(db: Session, user: User) -> DeliveryAddress | None:
    latest_order = db.scalar(
        select(Order)
        .options(selectinload(Order.delivery_address))
        .where(Order.user_id == user.id)
        .order_by(desc(Order.created_at))
    )
    if latest_order and latest_order.delivery_address:
        return latest_order.delivery_address

    return db.scalar(
        select(DeliveryAddress)
        .where(
            (DeliveryAddress.user_id == user.id)
            | (DeliveryAddress.recipient_phone == user.phone_number)
        )
        .order_by(desc(DeliveryAddress.created_at))
    )


async def _handle_location_pin(
    bot: TelegramBotClient,
    db: Session,
    chat_id: str,
    latitude: float,
    longitude: float,
) -> None:
    user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
    lang = user.preferred_lang if user else "en"
    t = BOT_TEXT.get(lang, BOT_TEXT["en"])

    if not user:
        await bot.send_message(chat_id, t["not_linked"], reply_markup=build_main_keyboard(lang))
        return

    coords = f"{latitude:.5f},{longitude:.5f}"

    # Update the latest active/undelivered order's address first, else latest saved address
    active_order = db.scalar(
        select(Order)
        .options(selectinload(Order.delivery_address))
        .where(
            Order.user_id == user.id,
            Order.status.in_(
                [OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.OUT_FOR_DELIVERY]
            ),
        )
        .order_by(desc(Order.created_at))
    )

    order_note = "Saved to your default Addis Ababa delivery profile for future orders."
    if active_order and active_order.delivery_address:
        active_order.delivery_address.gps_coordinates = coords
        db.commit()
        order_note = (
            f"Attached directly to active order *{active_order.order_number}* for your rider!"
        )
    else:
        saved_addr = _get_latest_user_address(db, user)
        if saved_addr:
            saved_addr.gps_coordinates = coords
            db.commit()
            order_note = f"Updated GPS pin on your *{saved_addr.sub_city}* delivery address."

    await bot.send_message(
        chat_id,
        t["gps_saved"].format(coords=coords, order_note=order_note),
        reply_markup=build_main_keyboard(lang),
    )

    # Alert dispatch admin chat if configured
    if settings.TELEGRAM_ADMIN_CHAT_ID:
        await bot.send_message(
            settings.TELEGRAM_ADMIN_CHAT_ID,
            f"📍 *Customer GPS Pin Received*\nParent: {user.full_name} ({user.phone_number})\n"
            f"Pin: `{coords}`\n{order_note}",
        )


async def _send_reorder_menu(bot: TelegramBotClient, db: Session, chat_id: str) -> None:
    user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
    lang = user.preferred_lang if user else "en"
    t = BOT_TEXT.get(lang, BOT_TEXT["en"])

    if not user:
        await bot.send_message(chat_id, t["not_linked"], reply_markup=build_main_keyboard(lang))
        return

    addr = _get_latest_user_address(db, user)
    if not addr:
        await bot.send_message(chat_id, t["no_address"], reply_markup=build_main_keyboard(lang))
        return

    # Fetch in-stock BABY_CARE items (diapers, wipes, bottles)
    baby_care_products = db.scalars(
        select(Product)
        .where(
            Product.is_active.is_(True),
            Product.category == ProductCategory.BABY_CARE,
            Product.stock_count > 0,
        )
        .order_by(Product.id.asc())
        .limit(6)
    ).all()

    if not baby_care_products:
        await bot.send_message(
            chat_id,
            "All diaper & wipes bundles are temporarily out of stock. Check https://lijnet.et shortly!",
            reply_markup=build_main_keyboard(lang),
        )
        return

    inline_rows: list[list[dict[str, str]]] = []
    for prod in baby_care_products:
        title = prod.title_am if lang == "am" else prod.title_en
        short_title = title[:36] + ("…" if len(title) > 36 else "")
        inline_rows.append(
            [
                {
                    "text": f"🛒 1x {short_title} ({prod.price_etb:,.0f} ETB)",
                    "callback_data": f"reorder_prod:{prod.id}",
                }
            ]
        )

    msg = t["reorder_header"].format(
        sub_city=addr.sub_city,
        woreda=addr.woreda,
        landmark=addr.landmark_description,
    )
    await bot.send_message(
        chat_id,
        msg,
        reply_markup={"inline_keyboard": inline_rows},
    )


async def _execute_one_click_reorder(
    bot: TelegramBotClient,
    db: Session,
    chat_id: str,
    product_id: int,
    callback_query_id: str | None = None,
) -> None:
    user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
    if not user:
        if callback_query_id:
            await bot.answer_callback_query(
                callback_query_id, "Please link your phone first.", show_alert=True
            )
        return

    saved_addr = _get_latest_user_address(db, user)
    if not saved_addr:
        if callback_query_id:
            await bot.answer_callback_query(
                callback_query_id, "No saved Addis Ababa address found.", show_alert=True
            )
        return

    prod = db.scalar(
        select(Product)
        .where(Product.id == product_id, Product.is_active.is_(True))
        .with_for_update()
    )
    if not prod or prod.stock_count < 1:
        db.rollback()
        if callback_query_id:
            await bot.answer_callback_query(
                callback_query_id, "Sorry, this item just sold out.", show_alert=True
            )
        return

    subtotal_etb = prod.price_etb
    delivery_fee_etb = calculate_delivery_fee(subtotal_etb, saved_addr.sub_city)
    total_etb = subtotal_etb + delivery_fee_etb

    # Default to COD if under 5,000 ETB limit, otherwise Chapa digital checkout
    pay_method = (
        PaymentMethod.COD if total_etb <= settings.COD_MAX_LIMIT_ETB else PaymentMethod.CHAPA
    )

    new_addr = DeliveryAddress(
        user_id=user.id,
        recipient_name=saved_addr.recipient_name,
        sub_city=saved_addr.sub_city,
        woreda=saved_addr.woreda,
        landmark_description=saved_addr.landmark_description,
        gps_coordinates=saved_addr.gps_coordinates,
        recipient_phone=saved_addr.recipient_phone,
    )
    db.add(new_addr)
    db.flush()

    now_year = datetime.now(timezone.utc).year
    order_number = f"LIJ-{now_year}-{uuid.uuid4().hex[:6].upper()}"
    tx_ref = f"LIJ-TX-{uuid.uuid4().hex[:10].upper()}"

    order = Order(
        order_number=order_number,
        user_id=user.id,
        delivery_address_id=new_addr.id,
        status=OrderStatus.CONFIRMED if pay_method == PaymentMethod.COD else OrderStatus.PENDING,
        payment_method=pay_method,
        payment_status=PaymentStatus.UNPAID,
        tx_ref=tx_ref,
        subtotal_etb=subtotal_etb,
        delivery_fee_etb=delivery_fee_etb,
        total_etb=total_etb,
    )
    db.add(order)
    db.flush()

    prod.stock_count -= 1
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=prod.id,
            quantity=1,
            unit_price_etb=prod.price_etb,
            line_total_etb=subtotal_etb,
        )
    )
    db.commit()

    pay_res = await initiate_payment(
        order=order,
        customer_email=user.email,
        recipient_name=new_addr.recipient_name,
        recipient_phone=new_addr.recipient_phone,
    )

    if callback_query_id:
        await bot.answer_callback_query(callback_query_id, f"Order {order_number} placed!")

    lang = user.preferred_lang
    item_title = prod.title_am if lang == "am" else prod.title_en
    payment_line = (
        f"💳 *Pay Online:* {pay_res.checkout_url}"
        if pay_method == PaymentMethod.CHAPA and pay_res.checkout_url
        else f"💵 *Payment:* {pay_method.value} ({pay_res.instructions or 'Pay on delivery'})"
    )

    confirmation = (
        f"🎉 *1-Click Reorder Confirmed!*\n"
        f"Order: *{order_number}*\n"
        f"Item: 1× {item_title}\n"
        f"Delivery ({new_addr.sub_city}): {delivery_fee_etb:,.2f} ETB\n"
        f"Total: *{total_etb:,.2f} ETB*\n"
        f"Landmark: _{new_addr.landmark_description}_\n\n"
        f"{payment_line}"
    )
    await bot.send_message(chat_id, confirmation, reply_markup=build_main_keyboard(lang))


async def _send_recent_orders(bot: TelegramBotClient, db: Session, chat_id: str) -> None:
    user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
    lang = user.preferred_lang if user else "en"
    t = BOT_TEXT.get(lang, BOT_TEXT["en"])

    if not user:
        await bot.send_message(chat_id, t["not_linked"], reply_markup=build_main_keyboard(lang))
        return

    orders = db.scalars(
        select(Order)
        .options(selectinload(Order.delivery_address))
        .where(Order.user_id == user.id)
        .order_by(desc(Order.created_at))
        .limit(5)
    ).all()

    if not orders:
        await bot.send_message(
            chat_id,
            "You have no orders on Lijnet yet. Browse https://lijnet.et to get started!",
            reply_markup=build_main_keyboard(lang),
        )
        return

    lines = ["📦 *Your Recent Lijnet Orders:*\n"]
    for o in orders:
        lines.append(
            f"• *{o.order_number}* — `{o.status.value}` ({o.payment_status.value})\n"
            f"  Total: {o.total_etb:,.2f} ETB → {o.delivery_address.sub_city}"
        )

    await bot.send_message(chat_id, "\n".join(lines), reply_markup=build_main_keyboard(lang))


async def process_telegram_update(update: dict[str, Any], db: Session) -> None:
    """Routes incoming Telegram webhook or polling updates."""
    bot = TelegramBotClient()

    # 1. Handle Inline Button Callbacks (e.g., 1-Click Reorder)
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = str(cb["message"]["chat"]["id"])
        data: str = cb.get("data", "")

        if data.startswith("reorder_prod:"):
            product_id = int(data.split(":", 1)[1])
            await _execute_one_click_reorder(bot, db, chat_id, product_id, callback_query_id=cb_id)
        return

    message = update.get("message")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    first_name = message.get("from", {}).get("first_name", "Parent")
    user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
    lang = user.preferred_lang if user else "en"
    t = BOT_TEXT.get(lang, BOT_TEXT["en"])

    # 2. Handle Shared Phone Contact
    if "contact" in message:
        phone_raw = message["contact"].get("phone_number", "")
        linked_user, err = _find_or_link_user_by_phone(db, chat_id, phone_raw, first_name)
        if err or not linked_user:
            await bot.send_message(chat_id, f"⚠️ {err}", reply_markup=build_main_keyboard(lang))
            return
        await bot.send_message(
            chat_id,
            t["linked_ok"].format(name=linked_user.full_name, phone=linked_user.phone_number),
            reply_markup=build_main_keyboard(linked_user.preferred_lang),
        )
        return

    # 3. Handle Shared Live GPS Pin
    if "location" in message:
        loc = message["location"]
        await _handle_location_pin(
            bot,
            db,
            chat_id,
            latitude=float(loc["latitude"]),
            longitude=float(loc["longitude"]),
        )
        return

    # 4. Handle Text Commands & ReplyKeyboard Buttons
    text = (message.get("text") or "").strip()
    if not text:
        return

    if text.startswith("/start") or text.startswith("/help"):
        await bot.send_message(chat_id, t["welcome"], reply_markup=build_main_keyboard(lang))
        return

    if text.startswith("/link"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await bot.send_message(
                chat_id,
                "Usage: `/link 0911223344` or tap *📱 Link Phone Number* below.",
                reply_markup=build_main_keyboard(lang),
            )
            return
        linked_user, err = _find_or_link_user_by_phone(db, chat_id, parts[1], first_name)
        if err or not linked_user:
            await bot.send_message(chat_id, f"⚠️ {err}", reply_markup=build_main_keyboard(lang))
            return
        await bot.send_message(
            chat_id,
            t["linked_ok"].format(name=linked_user.full_name, phone=linked_user.phone_number),
            reply_markup=build_main_keyboard(linked_user.preferred_lang),
        )
        return

    if "1-Click Diaper" in text or text.startswith("/reorder"):
        await _send_reorder_menu(bot, db, chat_id)
        return

    if "My Orders" in text or text.startswith("/orders"):
        await _send_recent_orders(bot, db, chat_id)
        return

    if "አማርኛ" in text or text.startswith("/lang"):
        new_lang = "am" if lang == "en" else "en"
        if user:
            user.preferred_lang = new_lang
            db.commit()
        new_t = BOT_TEXT[new_lang]
        await bot.send_message(
            chat_id, new_t["welcome"], reply_markup=build_main_keyboard(new_lang)
        )
        return

    await bot.send_message(chat_id, t["welcome"], reply_markup=build_main_keyboard(lang))


async def notify_order_event(db: Session, order: Order, event_title: str) -> None:
    """
    Sends an instant Telegram notification to the customer (matched by user_id or
    recipient_phone) whenever an order is created, paid, dispatched, or delivered.
    """
    bot = TelegramBotClient()
    user: User | None = None

    if order.user_id:
        user = db.scalar(select(User).where(User.id == order.user_id))
    if (not user or not user.telegram_chat_id) and order.delivery_address:
        user = db.scalar(
            select(User).where(
                User.phone_number == order.delivery_address.recipient_phone,
                User.telegram_chat_id.is_not(None),
            )
        )

    if not user or not user.telegram_chat_id:
        return

    addr = order.delivery_address
    gps_hint = (
        f"📍 GPS Pin: `{addr.gps_coordinates}`"
        if addr and addr.gps_coordinates
        else "💡 _Tip: Tap 📍 Share Delivery GPS Pin below to help our rider reach your gate faster._"
    )

    msg = (
        f"🔔 *{event_title}*\n"
        f"Order: *{order.order_number}*\n"
        f"Status: `{order.status.value}` | Payment: `{order.payment_status.value}`\n"
        f"Total: *{order.total_etb:,.2f} ETB*\n"
        f"Destination: {addr.sub_city if addr else 'Addis Ababa'}\n\n"
        f"{gps_hint}"
    )
    await bot.send_message(
        user.telegram_chat_id,
        msg,
        reply_markup=build_main_keyboard(user.preferred_lang),
    )


async def run_polling() -> None:
    """Local development polling loop (run with: python -m app.services.telegram_bot)."""
    bot = TelegramBotClient()
    if not bot.is_configured:
        print("Set TELEGRAM_BOT_TOKEN in .env first to run live Telegram polling.")
        return

    await bot.call_api("deleteWebhook", {"drop_pending_updates": False})
    print(f"@{settings.TELEGRAM_BOT_USERNAME} polling started... Press Ctrl+C to stop.")
    offset = 0

    while True:
        try:
            data = await bot.call_api(
                "getUpdates",
                {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
            )
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                db = SessionLocal()
                try:
                    await process_telegram_update(upd, db)
                finally:
                    db.close()
        except Exception as exc:
            logger.error("Telegram polling error: %s", exc)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run_polling())
