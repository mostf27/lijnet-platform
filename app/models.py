import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    VENDOR = "VENDOR"
    ADMIN = "ADMIN"


class ProductCategory(str, enum.Enum):
    BABY_CARE = "BABY_CARE"
    APPAREL = "APPAREL"
    EDUCATIONAL_TOYS = "EDUCATIONAL_TOYS"
    NURSERY = "NURSERY"


class AgeRange(str, enum.Enum):
    NEWBORN = "0-6m"
    INFANT = "6-12m"
    TODDLER = "1-3y"
    EARLY_LEARNER = "4-7y"
    JUNIOR = "8-12y"


class OriginType(str, enum.Enum):
    LOCAL_ETHIOPIAN = "LOCAL_ETHIOPIAN"
    IMPORTED = "IMPORTED"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class PaymentMethod(str, enum.Enum):
    TELEBIRR = "TELEBIRR"
    CHAPA = "CHAPA"
    SANTIMPAY = "SANTIMPAY"
    CBE_TRANSFER = "CBE_TRANSFER"
    COD = "COD"


class PaymentStatus(str, enum.Enum):
    UNPAID = "UNPAID"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), default=UserRole.CUSTOMER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    preferred_lang: Mapped[str] = mapped_column(String(5), default="en", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    products: Mapped[list["Product"]] = relationship(back_populates="vendor")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")
    addresses: Mapped[list["DeliveryAddress"]] = relationship(back_populates="user")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    title_en: Mapped[str] = mapped_column(String(200), nullable=False)
    title_am: Mapped[str] = mapped_column(String(200), nullable=False)
    description_en: Mapped[str] = mapped_column(Text, nullable=False)
    description_am: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[ProductCategory] = mapped_column(
        Enum(ProductCategory), index=True, nullable=False
    )
    age_range: Mapped[AgeRange] = mapped_column(Enum(AgeRange), index=True, nullable=False)
    origin_type: Mapped[OriginType] = mapped_column(Enum(OriginType), index=True, nullable=False)
    safety_certified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_hygiene_sealed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_starter_bundle: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    price_etb: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    vendor: Mapped["User"] = relationship(back_populates="products")
    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="product")


class DeliveryAddress(Base):
    __tablename__ = "delivery_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    sub_city: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    woreda: Mapped[str] = mapped_column(String(40), nullable=False)
    landmark_description: Mapped[str] = mapped_column(Text, nullable=False)
    gps_coordinates: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped["User | None"] = relationship(back_populates="addresses")
    orders: Mapped[list["Order"]] = relationship(back_populates="delivery_address")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_number: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    delivery_address_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_addresses.id"), nullable=False
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod), nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.UNPAID, nullable=False
    )
    tx_ref: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    subtotal_etb: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    delivery_fee_etb: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_etb: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    customer: Mapped["User | None"] = relationship(back_populates="orders")
    delivery_address: Mapped["DeliveryAddress"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_etb: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total_etb: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_opened_hygiene: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship(back_populates="order_items")
