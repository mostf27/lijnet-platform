import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ADDIS_SUBCITY_FEES, settings
from app.models import Product

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8")),
    "am": json.loads((LOCALES_DIR / "am.json").read_text(encoding="utf-8")),
}


def get_lang(request: Request) -> str:
    lang = request.cookies.get("lijnet_lang", "en")
    return lang if lang in _TRANSLATIONS else "en"


def get_translations(lang: str) -> dict[str, str]:
    return _TRANSLATIONS.get(lang, _TRANSLATIONS["en"])


def localize_product(product: Product, lang: str) -> dict[str, Any]:
    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title_am if lang == "am" else product.title_en,
        "title_en": product.title_en,
        "title_am": product.title_am,
        "description": product.description_am if lang == "am" else product.description_en,
        "category": product.category.value,
        "age_range": product.age_range.value,
        "origin_type": product.origin_type.value,
        "is_local": product.origin_type.value == "LOCAL_ETHIOPIAN",
        "safety_certified": product.safety_certified,
        "is_hygiene_sealed": product.is_hygiene_sealed,
        "is_starter_bundle": product.is_starter_bundle,
        "price_etb": product.price_etb,
        "stock_count": product.stock_count,
        "image_url": product.image_url,
    }


def calculate_delivery_fee(subtotal: Decimal, sub_city: str) -> Decimal:
    if subtotal <= Decimal("0.00"):
        return Decimal("0.00")
    if subtotal >= settings.FREE_DELIVERY_THRESHOLD_ETB:
        return Decimal("0.00")
    return ADDIS_SUBCITY_FEES.get(sub_city, Decimal("220.00"))


def build_cart_summary(request: Request, db: Session, sub_city: str = "Bole") -> dict[str, Any]:
    lang = get_lang(request)
    raw_cart: dict[str, int] = request.session.get("cart", {})
    product_ids = [int(pid) for pid in raw_cart.keys() if raw_cart[pid] > 0]

    items: list[dict[str, Any]] = []
    subtotal = Decimal("0.00")
    total_qty = 0

    if product_ids:
        products = db.scalars(
            select(Product).where(Product.id.in_(product_ids), Product.is_active.is_(True))
        ).all()
        prod_map = {p.id: p for p in products}
        for pid_str, qty in raw_cart.items():
            pid = int(pid_str)
            product = prod_map.get(pid)
            if not product or qty <= 0:
                continue
            clamped_qty = min(qty, max(product.stock_count, 1))
            line_total = product.price_etb * clamped_qty
            subtotal += line_total
            total_qty += clamped_qty
            items.append(
                {
                    "product": localize_product(product, lang),
                    "product_obj": product,
                    "quantity": clamped_qty,
                    "line_total_etb": line_total,
                }
            )

    delivery_fee = calculate_delivery_fee(subtotal, sub_city)
    total = subtotal + delivery_fee

    return {
        "items": items,
        "total_qty": total_qty,
        "subtotal_etb": subtotal,
        "delivery_fee_etb": delivery_fee,
        "total_etb": total,
        "sub_city": sub_city,
        "free_delivery_applied": subtotal >= settings.FREE_DELIVERY_THRESHOLD_ETB and subtotal > 0,
    }
