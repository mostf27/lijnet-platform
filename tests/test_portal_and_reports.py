from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
from app.routers.admin import _build_sales_ledger, _resolve_report_window
from app.services.auth import hash_password


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
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def portal_seed(db_session: Session) -> dict:
    admin = User(
        full_name="Lijnet Admin",
        email="admin@lijnet.et",
        phone_number="+251911000001",
        password_hash=hash_password("AdminPassword123!"),
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )
    seller_a = User(
        full_name="Sheba Cotton Kids PLC",
        email="seller.a@lijnet.et",
        phone_number="+251911223344",
        password_hash=hash_password("SellerPass123!"),
        role=UserRole.VENDOR,
        is_active=True,
        is_verified=True,
    )
    seller_b = User(
        full_name="Bole Toys Workshop",
        email="seller.b@lijnet.et",
        phone_number="+251911556677",
        password_hash=hash_password("SellerPass123!"),
        role=UserRole.VENDOR,
        is_active=True,
        is_verified=True,
    )
    buyer = User(
        full_name="Hermon Tesfaye",
        email="buyer@lijnet.et",
        phone_number="+251913445566",
        password_hash=hash_password("BuyerPass123!"),
        role=UserRole.CUSTOMER,
        is_active=True,
        is_verified=True,
    )
    db_session.add_all([admin, seller_a, seller_b, buyer])
    db_session.flush()

    prod_a = Product(
        sku="SHB-001",
        title_en="Highland Cotton Romper",
        title_am="የኢትዮጵያ ጥጥ ልብስ",
        description_en="Organic cotton romper",
        description_am="ኦርጋኒክ የጥጥ ልብስ",
        category=ProductCategory.APPAREL,
        age_range=AgeRange.NEWBORN,
        origin_type=OriginType.LOCAL_ETHIOPIAN,
        safety_certified=True,
        price_etb=Decimal("1500.00"),
        stock_count=10,
        image_url="/static/images/onesie.svg",
        vendor_id=seller_a.id,
        is_active=True,
    )
    prod_b = Product(
        sku="BOL-001",
        title_en="Amharic Alphabet Blocks",
        title_am="የአማርኛ ፊደል መጫወቻ",
        description_en="Wooden Fidel blocks",
        description_am="የእንጨት ፊደል መጫወቻ",
        category=ProductCategory.EDUCATIONAL_TOYS,
        age_range=AgeRange.TODDLER,
        origin_type=OriginType.LOCAL_ETHIOPIAN,
        safety_certified=True,
        price_etb=Decimal("1200.00"),
        stock_count=8,
        image_url="/static/images/fidel-puzzle.svg",
        vendor_id=seller_b.id,
        is_active=True,
    )
    db_session.add_all([prod_a, prod_b])
    db_session.flush()

    addr = DeliveryAddress(
        user_id=buyer.id,
        recipient_name="Hermon Tesfaye",
        sub_city="Bole",
        woreda="03",
        landmark_description="Near Bole Medhanialem, white gate",
        recipient_phone="+251913445566",
    )
    db_session.add(addr)
    db_session.flush()

    # Order 1: Paid Chapa order containing prod_a (1500) + delivery (180)
    order_paid = Order(
        order_number="LIJ-2026-LED01",
        user_id=buyer.id,
        delivery_address_id=addr.id,
        status=OrderStatus.DELIVERED,
        payment_method=PaymentMethod.CHAPA,
        payment_status=PaymentStatus.PAID,
        tx_ref="LIJ-TX-LED01",
        subtotal_etb=Decimal("1500.00"),
        delivery_fee_etb=Decimal("180.00"),
        total_etb=Decimal("1680.00"),
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    # Order 2: Unpaid COD order containing prod_b (1200) + delivery (180)
    order_cod = Order(
        order_number="LIJ-2026-LED02",
        user_id=buyer.id,
        delivery_address_id=addr.id,
        status=OrderStatus.CONFIRMED,
        payment_method=PaymentMethod.COD,
        payment_status=PaymentStatus.UNPAID,
        tx_ref="LIJ-TX-LED02",
        subtotal_etb=Decimal("1200.00"),
        delivery_fee_etb=Decimal("180.00"),
        total_etb=Decimal("1380.00"),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([order_paid, order_cod])
    db_session.flush()

    db_session.add_all(
        [
            OrderItem(
                order_id=order_paid.id,
                product_id=prod_a.id,
                quantity=1,
                unit_price_etb=Decimal("1500.00"),
                line_total_etb=Decimal("1500.00"),
            ),
            OrderItem(
                order_id=order_cod.id,
                product_id=prod_b.id,
                quantity=1,
                unit_price_etb=Decimal("1200.00"),
                line_total_etb=Decimal("1200.00"),
            ),
        ]
    )
    db_session.commit()

    return {
        "admin": admin,
        "seller_a": seller_a,
        "seller_b": seller_b,
        "buyer": buyer,
        "prod_a": prod_a,
        "prod_b": prod_b,
    }


def test_pre_cart_guest_gate_and_role_registration(client: TestClient, db_session: Session):
    # Unauthenticated guest visiting /cart is redirected to /login?next=/cart
    r_cart = client.get("/cart", follow_redirects=False)
    assert r_cart.status_code == 303
    assert "/login?next=/cart" in r_cart.headers["location"]

    # Register as Product Owner / Seller -> assigns VENDOR role & redirects to /seller
    r_reg = client.post(
        "/register",
        data={
            "account_type": "PRODUCT_OWNER",
            "full_name": "Merkato Kids Apparel",
            "email": "merkato@lijnet.et",
            "phone_number": "0911889900",
            "password": "Password123!",
            "next_url": "/",
        },
        follow_redirects=False,
    )
    assert r_reg.status_code == 303
    assert r_reg.headers["location"] == "/seller"

    user = db_session.scalar(select(User).where(User.email == "merkato@lijnet.et"))
    assert user is not None
    assert user.role == UserRole.VENDOR
    assert user.phone_number == "+251911889900"


def test_seller_portal_isolation_and_catalog_management(
    client: TestClient, db_session: Session, portal_seed: dict
):
    prod_b: Product = portal_seed["prod_b"]

    # Sign in as Seller A
    r_login = client.post(
        "/login",
        data={
            "identifier": "seller.a@lijnet.et",
            "password": "SellerPass123!",
            "next_url": "/seller",
        },
        follow_redirects=False,
    )
    assert r_login.status_code == 303

    # Seller A sees their own product (SHB-001) but NOT Seller B's product (BOL-001) in catalog
    r_portal = client.get("/seller")
    assert r_portal.status_code == 200
    assert "SHB-001" in r_portal.text
    assert "BOL-001" not in r_portal.text

    # Seller A can publish a new bilingual product
    r_create = client.post(
        "/seller/products/create",
        data={
            "sku": "SHB-002",
            "title_en": "Woven Cotton Bib Set",
            "title_am": "የጥጥ የሕፃን መመገቢያ ጨርቅ",
            "description_en": "Set of 3 soft cotton bibs",
            "description_am": "3 ለስላሳ የጥጥ ጨርቆች",
            "category": "APPAREL",
            "age_range": "0-6m",
            "origin_type": "LOCAL_ETHIOPIAN",
            "price_etb": "650.00",
            "stock_count": "25",
            "thumbnail_preset": "/static/images/onesie.svg",
            "image_url": "",
        },
        follow_redirects=False,
    )
    assert r_create.status_code == 303

    # Seller A cannot modify Seller B's product (404 ownership enforcement)
    r_forbidden = client.post(
        f"/seller/products/{prod_b.id}/update",
        data={
            "title_en": "Unauthorized Edit",
            "title_am": "ያልተፈቀደ",
            "price_etb": "10.00",
            "stock_count": "0",
            "is_active": "false",
        },
        follow_redirects=False,
    )
    assert r_forbidden.status_code == 404


def test_admin_user_management_and_period_sales_ledger(
    client: TestClient, db_session: Session, portal_seed: dict
):
    buyer: User = portal_seed["buyer"]

    # Sign in as Admin
    client.post(
        "/admin/login",
        data={
            "identifier": "admin@lijnet.et",
            "password": "AdminPassword123!",
            "next_url": "/admin",
        },
        follow_redirects=True,
    )

    # Promote Buyer to Product Owner / Seller (VENDOR) via User Management
    r_promote = client.post(
        f"/admin/users/{buyer.id}/update",
        data={
            "role": "VENDOR",
            "is_active": "true",
            "is_verified": "true",
            "new_password": "",
        },
        follow_redirects=False,
    )
    assert r_promote.status_code == 303
    db_session.refresh(buyer)
    assert buyer.role == UserRole.VENDOR

    # Verify Accounting Ledger Invariant across Daily, Weekly, Monthly, Yearly & All-Time
    orders = db_session.scalars(select(Order)).all()
    for period in ("daily", "weekly", "monthly", "yearly", "all"):
        win = _resolve_report_window(
            period=period,
            report_date=None,
            report_week=None,
            report_month=None,
            report_year=None,
        )
        ledger = _build_sales_ledger(
            orders,
            start_date=win["start_date"],
            end_date=win["end_date"],
        )
        assert (
            ledger["opening_balance_etb"] + ledger["total_debit_etb"] - ledger["total_credit_etb"]
            == ledger["closing_balance_etb"]
        )
