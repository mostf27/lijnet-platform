from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Lijnet (ልጅነት)"
    LEGAL_NAME: str = "Lijnet Digital Commerce PLC"
    APP_ENV: str = "development"
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    BASE_URL: str = "http://127.0.0.1:8000"

    # JWT Settings (Mobile App REST API)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/lijnet_db"

    # Payment Gateways
    CHAPA_SECRET_KEY: str = "CHASECK_TEST-placeholder"
    CHAPA_WEBHOOK_SECRET: str = "chapa-webhook-secret"
    CHAPA_BASE_URL: str = "https://api.chapa.co/v1"

    TELEBIRR_APP_ID: str = ""
    TELEBIRR_APP_KEY: str = ""
    TELEBIRR_SHORT_CODE: str = ""
    TELEBIRR_PUBLIC_KEY: str = ""
    TELEBIRR_BASE_URL: str = (
        "https://developerportal.ethiotelebirr.et:38443/apiaccess/payment/gateway"
    )

    SANTIMPAY_MERCHANT_ID: str = ""
    SANTIMPAY_PRIVATE_KEY: str = ""
    SANTIMPAY_BASE_URL: str = "https://services.santimpay.com/api/v1/gateway"

    # Telegram Bot (@lijnet_et)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = "lijnet_et"
    TELEGRAM_WEBHOOK_SECRET: str = "lijnet-tg-webhook-secret"
    TELEGRAM_ADMIN_CHAT_ID: str = ""

    # Ethiopian Ministry of Revenues (MoR) Tax & VAT Registration
    SELLER_TIN: str = "0078451290"
    SELLER_VAT_REG_NO: str = "1845920-01"
    SELLER_VAT_REG_DATE: str = "2024-05-15"
    SELLER_ADDRESS: str = "Addis Ababa, Bole Sub-City, Woreda 03, Ethiopia"
    VAT_RATE: Decimal = Decimal("0.15")

    # Ethiopian Business & Compliance Rules
    COD_MAX_LIMIT_ETB: Decimal = Decimal("5000.00")
    FREE_DELIVERY_THRESHOLD_ETB: Decimal = Decimal("4000.00")
    STATUTORY_RETURN_DAYS: int = 7  # Electronic Transaction Proclamation No. 1205/2020
    LOCAL_CONTENT_MIN_PCT: float = 50.0  # Proclamation No. 1205/2020 50% local mandate


settings = Settings()

# Addis Ababa Sub-City Delivery Fee Schedule (ETB)
ADDIS_SUBCITY_FEES: dict[str, Decimal] = {
    "Bole": Decimal("180.00"),
    "Kazanchis": Decimal("180.00"),
    "Yeka": Decimal("200.00"),
    "Kirkos": Decimal("200.00"),
    "Bole Bulbula": Decimal("220.00"),
    "Nifas Silk-Lafto": Decimal("230.00"),
    "Arada": Decimal("220.00"),
    "Lideta": Decimal("220.00"),
    "Lemi Kura": Decimal("250.00"),
    "Kolfe Keranio": Decimal("260.00"),
    "Gulele": Decimal("260.00"),
    "Akaky Kaliti": Decimal("280.00"),
}
