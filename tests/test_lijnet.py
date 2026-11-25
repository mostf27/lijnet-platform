import re
import zlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import (
    AgeRange,
    DeliveryAddress,
    Order,
    OrderItem,
    OrderStatus,
    OriginType,
    PaymentMethod,
    PaymentStatus,
    Product,
    ProductCategory,
    User,
    UserRole,
)
from app.services.auth import create_access_token, hash_password
from app.services.compliance import (
    calculate_local_content_compliance,
    evaluate_return_eligibility,
)
from app.services.invoices import _split_vat_inclusive, generate_vat_invoice_pdf

# ---------------------------------------------------------------------------
# Isolated In-Memory Database & FastAPI TestClient Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    # Instantiate directly without context manager so startup lifespan does not touch Postgres
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded_env(db_session: Session) -> dict:
    vendor = User(
        full_name="Sheba Cotton Kids PLC",
        email="vendor@lijnet.et",
        phone_number="+251911223344",
        password_hash=hash_password("VendorPass123!"),
        role=UserRole.VENDOR,
        is_active=True,
        is_verified=True,
    )
    customer = User(
        full_name="Selamawit Bekele",
        email="selam@example.et",
        phone_number="+251913445566",
        password_hash=hash_password("CustomerPassword123!"),
        role=UserRole.CUSTOMER,
        is_active=True,
        is_verified=True,
    )
    db_session.add_all([vendor, customer])
    db_session.flush()

    # 3 Local Ethiopian + 2 Imported = 60.0% Local Content
    p_local_1 = Product(
        sku="LIJ-LOC-101",
        title_en="Ethiopian Cotton Newborn Onesie",
        title_am="የኢትዮጵያ ጥጥ የጨቅላ ቦዲ",
        description_en="Organic highland cotton onesie",
        description_am="ኦርጋኒክ የኢትዮጵያ ጥጥ ልብስ",
        category=ProductCategory.APPAREL,
        age_range=AgeRange.NEWBORN,
        origin_type=OriginType.LOCAL_ETHIOPIAN,
        safety_certified=True,
        is_hygiene_sealed=False,
        is_starter_bundle=True,
        price_etb=Decimal("1850.00"),
        stock_count=2,  # Low stock to test atomic oversell prevention
        image_url="https://example.com/onesie.jpg",
        vendor_id=vendor.id,
        is_active=True,
    )
    p_local_2 = Product(
        sku="LIJ-LOC-102",
        title_en="Ge'ez Fidel Wooden Puzzle",
        title_am="የግዕዝ ፊደል የእንጨት መማሪያ",
        description_en="Non-toxic handcrafted Fidel puzzle",
        description_am="መርዛማ ያልሆነ የፊደል መማሪያ",
        category=ProductCategory.EDUCATIONAL_TOYS,
        age_range=AgeRange.TODDLER,
        origin_type=OriginType.LOCAL_ETHIOPIAN,
        safety_certified=True,
        is_hygiene_sealed=False,
        is_starter_bundle=False,
        price_etb=Decimal("1450.00"),
        stock_count=10,
        image_url="https://example.com/fidel.jpg",
        vendor_id=vendor.id,
        is_active=True,
    )
    p_local_3 = Product(
        sku="LIJ-LOC-103",
        title_en="Handwoven Cotton Nursery Basket",
        title_am="በእጅ የተሸመነ የጥጥ ቅርጫት",
        description_en="Woven organizer basket",
        description_am="የእቃ ማስቀመጫ ቅርጫት",
        category=ProductCategory.NURSERY,
        age_range=AgeRange.INFANT,
        origin_type=OriginType.LOCAL_ETHIOPIAN,
        safety_certified=True,
        is_hygiene_sealed=False,
        is_starter_bundle=False,
        price_etb=Decimal("2100.00"),
        stock_count=5,
        image_url="https://example.com/basket.jpg",
        vendor_id=vendor.id,
        is_active=True,
    )
    p_imp_hygiene = Product(
        sku="LIJ-IMP-201",
        title_en="Hypoallergenic Baby Diapers (72 Count)",
        title_am="ለስላሳ የሕፃናት ዳይፐር (72 ፍሬ)",
        description_en="Factory-sealed breathable diapers",
        description_am="በፋብሪካ የታሸገ ዳይፐር",
        category=ProductCategory.BABY_CARE,
        age_range=AgeRange.INFANT,
        origin_type=OriginType.IMPORTED,
        safety_certified=True,
        is_hygiene_sealed=True,
        is_starter_bundle=False,
        price_etb=Decimal("3250.00"),
        stock_count=15,
        image_url="https://example.com/diapers.jpg",
        vendor_id=vendor.id,
        is_active=True,
    )
    p_imp_crib = Product(
        sku="LIJ-IMP-202",
        title_en="Foldable Travel Crib with Mosquito Net",
        title_am="የሚታጠፍ የሕፃናት አልጋ",
        description_en="Lightweight travel bassinet",
        description_am="ቀላል የሕፃናት አልጋ",
        category=ProductCategory.NURSERY,
        age_range=AgeRange.NEWBORN,
        origin_type=OriginType.IMPORTED,
        safety_certified=True,
        is_hygiene_sealed=False,
        is_starter_bundle=False,
        price_etb=Decimal("18500.00"),
        stock_count=4,
        image_url="https://example.com/crib.jpg",
        vendor_id=vendor.id,
        is_active=True,
    )
    db_session.add_all([p_local_1, p_local_2, p_local_3, p_imp_hygiene, p_imp_crib])
    db_session.flush()

    address = DeliveryAddress(
        user_id=customer.id,
        recipient_name="Selamawit Bekele",
        sub_city="Bole",
        woreda="03",
        landmark_description="Near Edna Mall, behind Friendship Building, blue gate",
        gps_coordinates="8.9974,38.7867",
        recipient_phone="+251913445566",
    )
    db_session.add(address)
    db_session.flush()

    delivered_order = Order(
        order_number="LIJ-2026-TEST01",
        user_id=customer.id,
        delivery_address_id=address.id,
        status=OrderStatus.DELIVERED,
        payment_method=PaymentMethod.CHAPA,
        payment_status=PaymentStatus.PAID,
        tx_ref="LIJ-TX-TEST0001",
        subtotal_etb=Decimal("1450.00"),
        delivery_fee_etb=Decimal("180.00"),
        total_etb=Decimal("1630.00"),
        delivered_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    db_session.add(delivered_order)
    db_session.flush()

    db_session.add(
        OrderItem(
            order_id=delivered_order.id,
            product_id=p_local_2.id,
            quantity=1,
            unit_price_etb=Decimal("1450.00"),
            line_total_etb=Decimal("1450.00"),
            is_opened_hygiene=False,
        )
    )
    db_session.commit()

    return {
        "vendor": vendor,
        "customer": customer,
        "p_local_1": p_local_1,
        "p_local_2": p_local_2,
        "p_local_3": p_local_3,
        "p_imp_hygiene": p_imp_hygiene,
        "p_imp_crib": p_imp_crib,
        "address": address,
        "delivered_order": delivered_order,
    }


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Decompresses FlateDecode PDF content streams for text assertions."""
    chunks: list[str] = []
    for raw_stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, flags=re.DOTALL):
        try:
            chunks.append(zlib.decompress(raw_stream).decode("latin-1", errors="ignore"))
        except zlib.error:
            chunks.append(raw_stream.decode("latin-1", errors="ignore"))
    return "\n".join(chunks)


# ===========================================================================
# 1. PENNY-EXACT 15% VAT INVOICE & PDF GENERATION TESTS
# ===========================================================================


@pytest.mark.parametrize(
    ("gross_str", "expected_excl_str", "expected_vat_str"),
    [
        ("100.00", "86.96", "13.04"),
        ("1450.00", "1260.87", "189.13"),
        ("180.00", "156.52", "23.48"),
        ("1630.00", "1417.39", "212.61"),
        ("1850.00", "1608.70", "241.30"),
        ("3250.00", "2826.09", "423.91"),
        ("18500.00", "16086.96", "2413.04"),
        ("1.00", "0.87", "0.13"),
    ],
)
def test_split_vat_inclusive_penny_exact(
    gross_str: str, expected_excl_str: str, expected_vat_str: str
):
    gross = Decimal(gross_str)
    excl_vat, vat_amount = _split_vat_inclusive(gross, Decimal("0.15"))

    assert excl_vat == Decimal(expected_excl_str)
    assert vat_amount == Decimal(expected_vat_str)
    # Invariant: taxable base + 15% VAT must always equal the exact gross ETB amount
    assert excl_vat + vat_amount == gross


def test_generate_vat_invoice_pdf_mor_formatting_and_unicode_safety(
    db_session: Session, seeded_env: dict
):
    order: Order = seeded_env["delivered_order"]
    # Include Amharic text in landmark to verify Latin-1 PDF font sanitization never crashes
    order.delivery_address.landmark_description = "Near Edna Mall (ኤድና ሞል), Bole"
    db_session.commit()

    pdf_bytes = generate_vat_invoice_pdf(order, buyer_tin="0045129876")
    assert pdf_bytes.startswith(b"%PDF-")

    rendered_text = _extract_pdf_text(pdf_bytes)
    assert settings.SELLER_TIN in rendered_text
    assert settings.SELLER_VAT_REG_NO in rendered_text
    assert "0045129876" in rendered_text
    assert "LIJ-2026-TEST01" in rendered_text
    assert "1,417.39 ETB" in rendered_text  # 1,260.87 + 156.52
    assert "212.61 ETB" in rendered_text  # 189.13 + 23.48
    assert "1,630.00 ETB" in rendered_text
    assert "LIJ-MOR-" in rendered_text


def test_vat_invoice_pdf_endpoints(client: TestClient, seeded_env: dict):
    order: Order = seeded_env["delivered_order"]
    customer: User = seeded_env["customer"]

    # 1. Web storefront endpoint
    web_resp = client.get(f"/orders/{order.id}/invoice.pdf?buyer_tin=0099887766")
    assert web_resp.status_code == 200
    assert web_resp.headers["content-type"] == "application/pdf"
    assert web_resp.content.startswith(b"%PDF-")

    # 2. JWT-protected Mobile API endpoint
    token = create_access_token(customer)
    api_resp = client.get(
        f"/api/v1/orders/{order.id}/invoice.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert api_resp.status_code == 200
    assert api_resp.headers["content-type"] == "application/pdf"
    assert api_resp.content.startswith(b"%PDF-")


# ===========================================================================
# 2. PROCLAMATION NO. 1205/2020 COMPLIANCE TESTS (7-DAY RETURNS & 50% LOCAL)
# ===========================================================================


def test_return_eligibility_within_7_day_window(seeded_env: dict):
    order: Order = seeded_env["delivered_order"]
    order.delivered_at = datetime.now(timezone.utc) - timedelta(days=3)

    eligible, msg = evaluate_return_eligibility(order, hygiene_seal_broken=False)
    assert eligible is True
    assert "Proclamation No. 1205/2020" in msg


def test_return_ineligible_after_7_day_window_expires(seeded_env: dict):
    order: Order = seeded_env["delivered_order"]
    order.delivered_at = datetime.now(timezone.utc) - timedelta(days=7, minutes=5)

    eligible, msg = evaluate_return_eligibility(order, hygiene_seal_broken=False)
    assert eligible is False
    assert "expired" in msg.lower()


def test_return_ineligible_for_opened_hygiene_consumables(db_session: Session, seeded_env: dict):
    customer: User = seeded_env["customer"]
    address: DeliveryAddress = seeded_env["address"]
    diapers: Product = seeded_env["p_imp_hygiene"]

    hygiene_order = Order(
        order_number="LIJ-2026-HYG01",
        user_id=customer.id,
        delivery_address_id=address.id,
        status=OrderStatus.DELIVERED,
        payment_method=PaymentMethod.CHAPA,
        payment_status=PaymentStatus.PAID,
        tx_ref="LIJ-TX-HYG001",
        subtotal_etb=Decimal("3250.00"),
        delivery_fee_etb=Decimal("180.00"),
        total_etb=Decimal("3430.00"),
        delivered_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(hygiene_order)
    db_session.flush()

    db_session.add(
        OrderItem(
            order_id=hygiene_order.id,
            product_id=diapers.id,
            quantity=1,
            unit_price_etb=Decimal("3250.00"),
            line_total_etb=Decimal("3250.00"),
            is_opened_hygiene=True,
        )
    )
    db_session.commit()
    db_session.refresh(hygiene_order)

    # Unopened pack is eligible within 7 days
    ok_unopened, _ = evaluate_return_eligibility(hygiene_order, hygiene_seal_broken=False)
    assert ok_unopened is True

    # Opened hygiene seal is strictly blocked
    ok_opened, msg_opened = evaluate_return_eligibility(hygiene_order, hygiene_seal_broken=True)
    assert ok_opened is False
    assert "hygiene" in msg_opened.lower()


def test_return_ineligible_before_delivery(seeded_env: dict):
    order: Order = seeded_env["delivered_order"]
    order.status = OrderStatus.OUT_FOR_DELIVERY
    order.delivered_at = None

    eligible, msg = evaluate_return_eligibility(order, hygiene_seal_broken=False)
    assert eligible is False
    assert "DELIVERED" in msg


def test_local_content_50_percent_mandate(db_session: Session, seeded_env: dict):
    # Initial seeded catalog: 3 Local / 2 Imported = 60.0% -> Compliant
    report_60 = calculate_local_content_compliance(db_session)
    assert report_60.total_active_products == 5
    assert report_60.local_ethiopian_count == 3
    assert report_60.local_percentage == 60.0
    assert report_60.is_compliant is True

    # Deactivate one local product -> 2 Local / 2 Imported = 50.0% (Exact boundary) -> Compliant
    seeded_env["p_local_3"].is_active = False
    db_session.commit()
    report_50 = calculate_local_content_compliance(db_session)
    assert report_50.local_percentage == 50.0
    assert report_50.is_compliant is True

    # Deactivate another local product -> 1 Local / 2 Imported = 33.3% -> Non-Compliant
    seeded_env["p_local_2"].is_active = False
    db_session.commit()
    report_33 = calculate_local_content_compliance(db_session)
    assert report_33.local_percentage == 33.3
    assert report_33.is_compliant is False


# ===========================================================================
# 3. CHECKOUT STOCK LOCKING, OVERSELL PREVENTION & COD CEILING TESTS
# ===========================================================================


def test_postgresql_for_update_row_lock_sql_generation():
    """Verifies SQLAlchemy emits a genuine PostgreSQL SELECT ... FOR UPDATE lock."""
    stmt = select(Product).where(Product.id == 1).with_for_update()
    compiled_sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert compiled_sql.endswith("FOR UPDATE")


def test_mobile_checkout_atomic_stock_decrement_and_oversell_block(
    client: TestClient, db_session: Session, seeded_env: dict
):
    customer: User = seeded_env["customer"]
    onesie: Product = seeded_env["p_local_1"]
    assert onesie.stock_count == 2

    token = create_access_token(customer)
    headers = {"Authorization": f"Bearer {token}"}

    order_payload = {
        "items": [{"product_id": onesie.id, "quantity": 2}],
        "address": {
            "recipient_name": "Selamawit Bekele",
            "sub_city": "Bole",
            "woreda": "03",
            "landmark_description": "Near Bole Medhanialem Church, green gate",
            "recipient_phone": "0913445566",
        },
        "payment_method": "COD",
    }

    # First checkout claims all 2 units in stock -> 201 Created
    resp_1 = client.post("/api/v1/orders", json=order_payload, headers=headers)
    assert resp_1.status_code == 201
    data_1 = resp_1.json()
    assert Decimal(str(data_1["subtotal_etb"])) == Decimal("3700.00")
    assert Decimal(str(data_1["delivery_fee_etb"])) == Decimal("180.00")
    assert Decimal(str(data_1["total_etb"])) == Decimal("3880.00")

    db_session.refresh(onesie)
    assert onesie.stock_count == 0

    # Second checkout for the same item must be rejected with 409 Conflict (no negative stock)
    resp_2 = client.post("/api/v1/orders", json=order_payload, headers=headers)
    assert resp_2.status_code == 409
    assert "Insufficient stock" in resp_2.json()["detail"]

    db_session.refresh(onesie)
    assert onesie.stock_count == 0


def test_cod_max_ceiling_enforced_on_high_value_nursery_gear(
    client: TestClient, db_session: Session, seeded_env: dict
):
    customer: User = seeded_env["customer"]
    crib: Product = seeded_env["p_imp_crib"]  # 18,500 ETB (> 5,000 ETB COD limit)

    token = create_access_token(customer)
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "items": [{"product_id": crib.id, "quantity": 1}],
        "address": {
            "recipient_name": "Selamawit Bekele",
            "sub_city": "Yeka",
            "woreda": "08",
            "landmark_description": "CMC Michael Square, behind Sunshine Real Estate",
            "recipient_phone": "+251913445566",
        },
        "payment_method": "COD",
    }

    resp = client.post("/api/v1/orders", json=payload, headers=headers)
    assert resp.status_code == 400
    assert "Cash on Delivery is capped" in resp.json()["detail"]

    # Stock must remain untouched after COD rejection
    db_session.refresh(crib)
    assert crib.stock_count == 4
