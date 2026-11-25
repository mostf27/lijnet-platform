from decimal import Decimal
from typing import Any

import flet as ft

from app_client.api import LijnetApiClient
from app_client.theme import ACCENT, ECO

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "brand": "Lijnet",
        "brand_am": "ልጅነት",
        "tagline": "Safe. Authentic. Delivered for Every Stage of Childhood.",
        "tab_shop": "Shop",
        "tab_checkout": "Checkout",
        "tab_orders": "Orders",
        "all_ages": "All (0–12)",
        "made_in_ethiopia": "Made in Ethiopia",
        "verified_import": "Verified Import",
        "verified_safe": "Lijnet Verified Safe",
        "add_to_cart": "Add to Cart",
        "search_hint": "Search diapers, Ethiopian cotton, ዳይፐር...",
        "checkout_title": "Addis Ababa Landmark Checkout",
        "recipient": "Recipient Full Name",
        "phone": "Ethiopian Mobile (+2519... / 09...)",
        "sub_city": "Addis Ababa Sub-City",
        "woreda": "Woreda",
        "landmark": "Landmark / Gate Directions",
        "gps_pin": "GPS Coordinates (Optional lat,lng)",
        "payment_method": "Select Payment Method",
        "place_order": "Confirm & Place Order",
        "subtotal": "Subtotal",
        "delivery_fee": "Delivery Fee",
        "free_delivery": "FREE (>= 4,000 ETB)",
        "total": "Total",
        "orders_title": "My Orders & 7-Day Returns",
        "request_return": "Request 7-Day Return",
        "sign_out": "Sign Out",
    },
    "am": {
        "brand": "ልጅነት",
        "brand_am": "Lijnet",
        "tagline": "ለልጅዎ ደህንነት እና እድገት የታመነ ምርጫ።",
        "tab_shop": "መደብር",
        "tab_checkout": "ክፍያ",
        "tab_orders": "ትዕዛዞች",
        "all_ages": "ሁሉም (0–12)",
        "made_in_ethiopia": "በኢትዮጵያ የተመረተ",
        "verified_import": "የተረጋገጠ የውጭ ምርት",
        "verified_safe": "በልጅነት የተረጋገጠ",
        "add_to_cart": "ወደ ጋሪ ጨምር",
        "search_hint": "ዳይፐር፣ የኢትዮጵያ ጥጥ፣ ፊደል ይፈልጉ...",
        "checkout_title": "የአዲስ አበባ አድራሻ እና ክፍያ",
        "recipient": "የተቀባዩ ሙሉ ስም",
        "phone": "የኢትዮጵያ ስልክ (+2519... / 09...)",
        "sub_city": "ክፍለ ከተማ",
        "woreda": "ወረዳ",
        "landmark": "ልዩ ምልክት / የበር አቅጣጫ",
        "gps_pin": "የጂፒኤስ አቅጣጫ (አማራጭ)",
        "payment_method": "የክፍያ ዘዴ ይምረጡ",
        "place_order": "ትዕዛዝ አረጋግጥ",
        "subtotal": "ንዑስ ድምር",
        "delivery_fee": "የማድረሻ ክፍያ",
        "free_delivery": "ነፃ ማድረሻ (ከ 4,000 ብር በላይ)",
        "total": "ጠቅላላ ድምር",
        "orders_title": "ትዕዛዞቼ እና የ7 ቀን መመለሻ",
        "request_return": "የ7 ቀን መመለሻ ጠይቅ",
        "sign_out": "ውጣ",
    },
}


class AppState:
    """Central reactive state for the pure-Python Lijnet mobile app."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.api = LijnetApiClient()
        self.lang: str = "en"
        self.user: dict[str, Any] | None = None
        self.cart: dict[int, dict[str, Any]] = {}
        self.selected_sub_city: str = "Bole"

    @property
    def t(self) -> dict[str, str]:
        return STRINGS[self.lang]

    def show_toast(self, message: str, is_error: bool = False) -> None:
        sb = ft.SnackBar(
            content=ft.Text(message, color="#FFFFFF", weight=ft.FontWeight.W_600),
            bgcolor=ACCENT if is_error else ECO,
            duration=3000,
        )
        if hasattr(self.page, "show_dialog"):
            self.page.show_dialog(sb)
        else:
            self.page.overlay.append(sb)
            sb.open = True
            self.page.update()

    def add_to_cart(self, product: dict[str, Any], qty: int = 1) -> None:
        pid = int(product["id"])
        max_stock = int(product.get("stock_count", 1))
        if max_stock <= 0:
            self.show_toast("Item is out of stock.", is_error=True)
            return

        if pid in self.cart:
            new_qty = min(self.cart[pid]["quantity"] + qty, max_stock)
            self.cart[pid]["quantity"] = new_qty
            self.cart[pid]["product"] = product
        else:
            self.cart[pid] = {"product": product, "quantity": min(qty, max_stock)}

    def update_cart_qty(self, product_id: int, qty: int) -> None:
        if qty <= 0:
            self.cart.pop(product_id, None)
        elif product_id in self.cart:
            max_stock = int(self.cart[product_id]["product"].get("stock_count", 1))
            self.cart[product_id]["quantity"] = min(qty, max_stock)

    def clear_cart(self) -> None:
        self.cart.clear()

    @property
    def cart_count(self) -> int:
        return sum(entry["quantity"] for entry in self.cart.values())

    @property
    def cart_subtotal(self) -> Decimal:
        total = Decimal("0.00")
        for entry in self.cart.values():
            price = Decimal(str(entry["product"]["price_etb"]))
            total += price * int(entry["quantity"])
        return total
