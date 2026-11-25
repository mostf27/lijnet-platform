import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import ADDIS_SUBCITY_FEES, settings

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "platform_settings.json"


def apply_persisted_settings() -> None:
    """Loads saved operational overrides from disk into runtime settings if present."""
    if not SETTINGS_FILE.exists():
        return
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return

    if "legal_name" in data:
        settings.LEGAL_NAME = str(data["legal_name"])
    if "seller_tin" in data:
        settings.SELLER_TIN = str(data["seller_tin"])
    if "seller_vat_reg_no" in data:
        settings.SELLER_VAT_REG_NO = str(data["seller_vat_reg_no"])
    if "seller_vat_reg_date" in data:
        settings.SELLER_VAT_REG_DATE = str(data["seller_vat_reg_date"])
    if "seller_address" in data:
        settings.SELLER_ADDRESS = str(data["seller_address"])
    if "free_delivery_threshold_etb" in data:
        settings.FREE_DELIVERY_THRESHOLD_ETB = Decimal(str(data["free_delivery_threshold_etb"]))
    if "cod_max_limit_etb" in data:
        settings.COD_MAX_LIMIT_ETB = Decimal(str(data["cod_max_limit_etb"]))
    if "statutory_return_days" in data:
        settings.STATUTORY_RETURN_DAYS = int(data["statutory_return_days"])
    if "local_content_min_pct" in data:
        settings.LOCAL_CONTENT_MIN_PCT = float(data["local_content_min_pct"])

    if isinstance(data.get("subcity_fees"), dict):
        for sub_city, fee in data["subcity_fees"].items():
            if sub_city in ADDIS_SUBCITY_FEES:
                ADDIS_SUBCITY_FEES[sub_city] = Decimal(str(fee))


def get_effective_settings() -> dict[str, Any]:
    """Returns current operational, tax, delivery, and integration settings for the UI."""
    apply_persisted_settings()
    return {
        "legal_name": settings.LEGAL_NAME,
        "seller_tin": settings.SELLER_TIN,
        "seller_vat_reg_no": settings.SELLER_VAT_REG_NO,
        "seller_vat_reg_date": settings.SELLER_VAT_REG_DATE,
        "seller_address": settings.SELLER_ADDRESS,
        "free_delivery_threshold_etb": settings.FREE_DELIVERY_THRESHOLD_ETB,
        "cod_max_limit_etb": settings.COD_MAX_LIMIT_ETB,
        "statutory_return_days": settings.STATUTORY_RETURN_DAYS,
        "local_content_min_pct": settings.LOCAL_CONTENT_MIN_PCT,
        "subcity_fees": dict(ADDIS_SUBCITY_FEES),
        "base_url": settings.BASE_URL,
        "telegram_username": settings.TELEGRAM_BOT_USERNAME,
        "telegram_configured": bool(
            settings.TELEGRAM_BOT_TOKEN and not settings.TELEGRAM_BOT_TOKEN.startswith("paste-your")
        ),
        "chapa_configured": bool(
            settings.CHAPA_SECRET_KEY and not settings.CHAPA_SECRET_KEY.endswith("placeholder")
        ),
    }


def save_platform_settings(updates: dict[str, Any]) -> None:
    """Merges updates into app/platform_settings.json and reloads runtime config."""
    current: dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        try:
            current = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            current = {}

    current.update(updates)
    SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    apply_persisted_settings()


# Load persisted overrides automatically at import time
apply_persisted_settings()
