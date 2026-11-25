import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.config import ADDIS_SUBCITY_FEES
from app.models import (
    AgeRange,
    OrderStatus,
    OriginType,
    PaymentMethod,
    PaymentStatus,
    ProductCategory,
    UserRole,
)

ETHIOPIAN_PHONE_REGEX = re.compile(r"^\+251[79]\d{8}$")


def validate_ethiopian_phone(value: str) -> str:
    cleaned = value.strip().replace(" ", "").replace("-", "")
    if cleaned.startswith("09") or cleaned.startswith("07"):
        cleaned = "+251" + cleaned[1:]
    if not ETHIOPIAN_PHONE_REGEX.match(cleaned):
        raise ValueError(
            "Phone number must be a valid Ethiopian mobile number (+2519XXXXXXXX or +2517XXXXXXXX)."
        )
    return cleaned


class UserCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    phone_number: str
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.CUSTOMER

    @field_validator("phone_number")
    @classmethod
    def check_phone(cls, v: str) -> str:
        return validate_ethiopian_phone(v)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    phone_number: str
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime


class ProductCreate(BaseModel):
    sku: str = Field(..., min_length=3, max_length=50)
    title_en: str = Field(..., min_length=3, max_length=200)
    title_am: str = Field(..., min_length=2, max_length=200)
    description_en: str = Field(..., min_length=10)
    description_am: str = Field(..., min_length=5)
    category: ProductCategory
    age_range: AgeRange
    origin_type: OriginType
    safety_certified: bool = True
    is_hygiene_sealed: bool = False
    is_starter_bundle: bool = False
    price_etb: Decimal = Field(..., gt=0)
    stock_count: int = Field(..., ge=0)
    image_url: str = ""
    vendor_id: int


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    title_en: str
    title_am: str
    description_en: str
    description_am: str
    category: ProductCategory
    age_range: AgeRange
    origin_type: OriginType
    safety_certified: bool
    is_hygiene_sealed: bool
    is_starter_bundle: bool
    price_etb: Decimal
    stock_count: int
    image_url: str
    vendor_id: int
    is_active: bool


class DeliveryAddressCreate(BaseModel):
    recipient_name: str = Field(..., min_length=2, max_length=120)
    sub_city: str
    woreda: str = Field(..., min_length=1, max_length=40)
    landmark_description: str = Field(..., min_length=8, max_length=400)
    gps_coordinates: str | None = Field(default=None, max_length=64)
    recipient_phone: str

    @field_validator("sub_city")
    @classmethod
    def check_sub_city(cls, v: str) -> str:
        if v not in ADDIS_SUBCITY_FEES:
            valid = ", ".join(ADDIS_SUBCITY_FEES.keys())
            raise ValueError(f"Unsupported Addis Ababa sub-city '{v}'. Choose from: {valid}")
        return v

    @field_validator("recipient_phone")
    @classmethod
    def check_recipient_phone(cls, v: str) -> str:
        return validate_ethiopian_phone(v)


class CheckoutRequest(BaseModel):
    address: DeliveryAddressCreate
    payment_method: PaymentMethod
    customer_email: EmailStr | None = None


class ReturnRequestSchema(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    hygiene_seal_broken: bool = False


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    created_at: datetime


class LocalComplianceReport(BaseModel):
    total_active_products: int
    local_ethiopian_count: int
    imported_count: int
    local_percentage: float
    imported_percentage: float
    min_required_percentage: float
    is_compliant: bool
