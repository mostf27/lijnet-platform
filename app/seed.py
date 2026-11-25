import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from passlib.context import CryptContext
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
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

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def seed_database() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # 1. Seed Users (Admin, Local Vendors, Sample Customer)
        users_data = [
            {
                "full_name": "Lijnet Platform Admin",
                "email": "admin@lijnet.et",
                "phone_number": "+251911000001",
                "password": "AdminPassword123!",
                "role": UserRole.ADMIN,
                "is_verified": True,
            },
            {
                "full_name": "Sheba Cotton Kids Wear PLC",
                "email": "vendor.cotton@lijnet.et",
                "phone_number": "+251911223344",
                "password": "VendorPassword123!",
                "role": UserRole.VENDOR,
                "is_verified": True,
            },
            {
                "full_name": "Addis Fidel Montessori Crafts",
                "email": "vendor.toys@lijnet.et",
                "phone_number": "+251922334455",
                "password": "VendorPassword123!",
                "role": UserRole.VENDOR,
                "is_verified": True,
            },
            {
                "full_name": "Selamawit Bekele",
                "email": "selam@example.et",
                "phone_number": "+251913445566",
                "password": "CustomerPassword123!",
                "role": UserRole.CUSTOMER,
                "is_verified": True,
            },
        ]

        user_map: dict[str, User] = {}
        for u in users_data:
            existing = db.scalar(select(User).where(User.email == u["email"]))
            if not existing:
                existing = User(
                    full_name=u["full_name"],
                    email=u["email"],
                    phone_number=u["phone_number"],
                    password_hash=pwd_context.hash(u["password"]),
                    role=u["role"],
                    is_active=True,
                    is_verified=u["is_verified"],
                )
                db.add(existing)
                db.flush()
            user_map[u["email"]] = existing

        cotton_vendor = user_map["vendor.cotton@lijnet.et"]
        toy_vendor = user_map["vendor.toys@lijnet.et"]
        admin_user = user_map["admin@lijnet.et"]
        customer = user_map["selam@example.et"]

        # 2. Seed Products (6 LOCAL_ETHIOPIAN + 4 IMPORTED = 60% Local Compliance)
        products_data = [
            {
                "sku": "LIJ-LOC-001",
                "title_en": "100% Ethiopian Cotton Newborn Onesie Set (3-Pack)",
                "title_am": "100% የኢትዮጵያ ጥጥ የጨቅላ ሕፃናት ቦዲ ስብስብ (3 ቁራጭ)",
                "description_en": "Ultra-soft, breathable organic Ethiopian cotton onesies tailored for Addis Ababa's highland climate. Tagless neck and nickel-free snaps.",
                "description_am": "ለአዲስ አበባ የአየር ንብረት ተስማሚ የሆነ እጅግ ለስላሳ እና አየር የሚያስገባ 100% የኢትዮጵያ ጥጥ የሕፃናት ልብስ።",
                "category": ProductCategory.APPAREL,
                "age_range": AgeRange.NEWBORN,
                "origin_type": OriginType.LOCAL_ETHIOPIAN,
                "safety_certified": True,
                "is_hygiene_sealed": False,
                "is_starter_bundle": True,
                "price_etb": Decimal("1850.00"),
                "stock_count": 45,
                "image_url": "https://images.unsplash.com/photo-1522771930-78848d9293e8?w=600&q=80",
                "vendor_id": cotton_vendor.id,
            },
            {
                "sku": "LIJ-LOC-002",
                "title_en": "Ge'ez Fidel Wooden Montessori Puzzle Board",
                "title_am": "የግዕዝ ፊደል የእንጨት ሞንቴሶሪ መማሪያ ሰሌዳ",
                "description_en": "Handcrafted in Addis Ababa from smooth-sanded local hardwood with child-safe, non-toxic water-based paints. Teaches Amharic Fidel recognition.",
                "description_am": "አዲስ አበባ ውስጥ በጥንቃቄ ከተጠረበ እንጨት እና መርዛማ ካልሆነ ቀለም የተሰራ የአማርኛ ፊደል መማሪያ መጫወቻ።",
                "category": ProductCategory.EDUCATIONAL_TOYS,
                "age_range": AgeRange.TODDLER,
                "origin_type": OriginType.LOCAL_ETHIOPIAN,
                "safety_certified": True,
                "is_hygiene_sealed": False,
                "is_starter_bundle": False,
                "price_etb": Decimal("1450.00"),
                "stock_count": 32,
                "image_url": "https://images.unsplash.com/photo-1596461404969-9ae70f2830c1?w=600&q=80",
                "vendor_id": toy_vendor.id,
            },
            {
                "sku": "LIJ-LOC-003",
                "title_en": "Lijnet Addis Newborn Starter Box (8 Essentials)",
                "title_am": "የልጅነት የጨቅላ ሕፃን መነሻ ስጦታ ሳጥን (8 ዋና እቃዎች)",
                "description_en": "Curated hospital-bag box featuring 2 Ethiopian cotton rompers, muslin swaddle, baby hat, milestone cards, and sensitive wipes.",
                "description_am": "ለአዲስ የተወለዱ ሕፃናት እና ለአራስ ጥየቃ የተዘጋጀ 8 ዋና ዋና እቃዎችን የያዘ የስጦታ ሳጥን።",
                "category": ProductCategory.BABY_CARE,
                "age_range": AgeRange.NEWBORN,
                "origin_type": OriginType.LOCAL_ETHIOPIAN,
                "safety_certified": True,
                "is_hygiene_sealed": False,
                "is_starter_bundle": True,
                "price_etb": Decimal("4950.00"),
                "stock_count": 18,
                "image_url": "https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?w=600&q=80",
                "vendor_id": admin_user.id,
            },
            {
                "sku": "LIJ-LOC-004",
                "title_en": "Bilingual Amharic-English Animal Storybook Set (4 Books)",
                "title_am": "ባለሁለት ቋንቋ የአማርኛ እና እንግሊዝኛ የእንስሳት ተረት መጻሕፍት (4 መጻሕፍት)",
                "description_en": "Thick tear-resistant board books featuring Ethiopian wildlife (Walia Ibex, Gelada Baboon) in vibrant illustrations.",
                "description_am": "የኢትዮጵያን ብርቅዬ እንስሳት በቀለማት ያሸበረቁ ስዕሎች በአማርኛ እና በእንግሊዝኛ የሚያስተምሩ ጠንካራ መጻሕፍት።",
                "category": ProductCategory.EDUCATIONAL_TOYS,
                "age_range": AgeRange.EARLY_LEARNER,
                "origin_type": OriginType.LOCAL_ETHIOPIAN,
                "safety_certified": True,
                "is_hygiene_sealed": False,
                "is_starter_bundle": False,
                "price_etb": Decimal("1200.00"),
                "stock_count": 60,
                "image_url": "https://images.unsplash.com/photo-1512820790803-83ca734da794?w=600&q=80",
                "vendor_id": toy_vendor.id,
            },
            {
                "sku": "LIJ-LOC-005",
                "title_en": "Handwoven Cotton Nursery Storage Basket",
                "title_am": "በእጅ የተሸመነ የጥጥ የሕፃናት ክፍል እቃ ማስቀመጫ ቅርጫት",
                "description_en": "Soft, sturdy woven organizer basket made by Addis Ababa women artisans. Ideal for diapers, toys, and laundry.",
                "description_am": "በአዲስ አበባ ሴት የእጅ ባለሙያዎች የተሰራ ለስላሳ እና ጠንካራ የዳይፐር እና የመጫወቻ ማስቀመጫ ቅርጫት።",
                "category": ProductCategory.NURSERY,
                "age_range": AgeRange.INFANT,
                "origin_type": OriginType.LOCAL_ETHIOPIAN,
                "safety_certified": True,
                "is_hygiene_sealed": False,
                "is_starter_bundle": False,
                "price_etb": Decimal("2100.00"),
                "stock_count": 22,
                "image_url": "https://images.unsplash.com/photo-1586023492125-27b2c045efd7?w=600&q=80",
                "vendor_id": cotton_vendor.id,
            },
            {
                "sku": "LIJ-LOC-006",
                "title_en": "Junior School-Age Ethiopian Cotton Hoodie & Jogger Set",
                "title_am": "ለትምህርት ቤት እድሜ ልጆች የኢትዮጵያ ጥጥ ሹራብ እና ሱሪ ስብስብ",
                "description_en": "Warm fleece-lined cotton tracksuit designed for chilly Addis Ababa mornings. Pre-shrunk and machine washable.",
                "description_am": "ለአዲስ አበባ ቀዝቃዛ ማለዳዎች የሚሆን ሞቃት እና ምቹ የጥጥ ሹራብ እና ሱሪ ስብስብ።",
                "category": ProductCategory.APPAREL,
                "age_range": AgeRange.JUNIOR,
                "origin_type": OriginType.LOCAL_ETHIOPIAN,
                "safety_certified": True,
                "is_hygiene_sealed": False,
                "is_starter_bundle": False,
                "price_etb": Decimal("2950.00"),
                "stock_count": 28,
                "image_url": "https://images.unsplash.com/photo-1519238263530-99bdd11df2ea?w=600&q=80",
                "vendor_id": cotton_vendor.id,
            },
            {
                "sku": "LIJ-IMP-001",
                "title_en": "Sensitive Hypoallergenic Baby Diapers — Size 3 (72 Count)",
                "title_am": "ለስላሳ የሕፃናት ዳይፐር — ቁጥር 3 (72 ፍሬ)",
                "description_en": "Batch-verified authentic breathable diapers with 12-hour dryness lock. Factory sealed; minimum 12 months remaining shelf life.",
                "description_am": "ትክክለኛነቱ የተረጋገጠ እስከ 12 ሰዓት ደረቅነትን የሚጠብቅ የሕፃናት ዳይፐር። በፋብሪካ የታሸገ።",
                "category": ProductCategory.BABY_CARE,
                "age_range": AgeRange.INFANT,
                "origin_type": OriginType.IMPORTED,
                "safety_certified": True,
                "is_hygiene_sealed": True,
                "is_starter_bundle": False,
                "price_etb": Decimal("3250.00"),
                "stock_count": 80,
                "image_url": "https://images.unsplash.com/photo-1544126592-807ade215a0b?w=600&q=80",
                "vendor_id": admin_user.id,
            },
            {
                "sku": "LIJ-IMP-002",
                "title_en": "BPA-Free Anti-Colic Silicone Feeding Bottle Set (2-Pack)",
                "title_am": "ከBPA ነፃ የሆነ ፀረ-ቁርጠት የሕፃናት ጡጦ ስብስብ (2 ፍሬ)",
                "description_en": "Food-grade silicone anti-colic bottles (240ml) with slow-flow nipples. Certified BPA, Phthalate, and Lead free.",
                "description_am": "ለሕፃናት ደህንነቱ የተረጋገጠ ከBPA ነፃ የሆነ ፀረ-ቁርጠት የሲሊኮን ጡጦ (240ml)።",
                "category": ProductCategory.BABY_CARE,
                "age_range": AgeRange.NEWBORN,
                "origin_type": OriginType.IMPORTED,
                "safety_certified": True,
                "is_hygiene_sealed": True,
                "is_starter_bundle": True,
                "price_etb": Decimal("2400.00"),
                "stock_count": 35,
                "image_url": "https://images.unsplash.com/photo-1584839404042-8bc21d240e91?w=600&q=80",
                "vendor_id": admin_user.id,
            },
            {
                "sku": "LIJ-IMP-003",
                "title_en": "99% Pure Water Baby Wipes (3-Pack Value Bundle)",
                "title_am": "99% ንፁህ ውሃ የሕፃናት መጥረጊያ ዋይፕስ (3 ፓኬት)",
                "description_en": "Fragrance-free, alcohol-free dermatologically tested water wipes for sensitive newborn skin.",
                "description_am": "ሽቶ እና አልኮል የሌለው ለጨቅላ ሕፃናት ስስ ቆዳ ተስማሚ የሆነ ንፁህ የውሃ ዋይፕስ።",
                "category": ProductCategory.BABY_CARE,
                "age_range": AgeRange.NEWBORN,
                "origin_type": OriginType.IMPORTED,
                "safety_certified": True,
                "is_hygiene_sealed": True,
                "is_starter_bundle": False,
                "price_etb": Decimal("1650.00"),
                "stock_count": 65,
                "image_url": "https://images.unsplash.com/photo-1555252333-9f8e92e65df9?w=600&q=80",
                "vendor_id": admin_user.id,
            },
            {
                "sku": "LIJ-IMP-004",
                "title_en": "Foldable Travel Crib & Bassinet with Mosquito Net",
                "title_am": "የሚታጠፍ የሕፃናት አልጋ ከትንኝ መከላከያ መረብ ጋር",
                "description_en": "Lightweight aluminum-frame nursery crib with breathable mesh sides, changing table attachment, and compact carry bag.",
                "description_am": "ቀላል እና የሚታጠፍ የሕፃናት አልጋ ከአየር ማስገቢያ መረብ እና የትንኝ መከላከያ ጋር።",
                "category": ProductCategory.NURSERY,
                "age_range": AgeRange.NEWBORN,
                "origin_type": OriginType.IMPORTED,
                "safety_certified": True,
                "is_hygiene_sealed": False,
                "is_starter_bundle": False,
                "price_etb": Decimal("18500.00"),
                "stock_count": 8,
                "image_url": "https://images.unsplash.com/photo-1522771739844-6a9f6d5f14af?w=600&q=80",
                "vendor_id": admin_user.id,
            },
        ]

        seeded_products: list[Product] = []
        for p in products_data:
            existing = db.scalar(select(Product).where(Product.sku == p["sku"]))
            if not existing:
                existing = Product(**p)
                db.add(existing)
                db.flush()
            seeded_products.append(existing)

        # 3. Seed One Sample Delivered Order (3 days ago) to test 7-day Return Window
        sample_order_num = "LIJ-2026-1001"
        if not db.scalar(select(Order).where(Order.order_number == sample_order_num)):
            addr = DeliveryAddress(
                user_id=customer.id,
                recipient_name=customer.full_name,
                sub_city="Bole",
                woreda="03",
                landmark_description="Near Edna Mall, behind Friendship Business Center, blue gate",
                gps_coordinates="8.9974,38.7867",
                recipient_phone=customer.phone_number,
            )
            db.add(addr)
            db.flush()

            item_prod = seeded_products[1]  # Ge'ez Fidel Wooden Puzzle
            order = Order(
                order_number=sample_order_num,
                user_id=customer.id,
                delivery_address_id=addr.id,
                status=OrderStatus.DELIVERED,
                payment_method=PaymentMethod.CHAPA,
                payment_status=PaymentStatus.PAID,
                tx_ref=f"LIJ-TX-{uuid.uuid4().hex[:10].upper()}",
                subtotal_etb=item_prod.price_etb,
                delivery_fee_etb=Decimal("180.00"),
                total_etb=item_prod.price_etb + Decimal("180.00"),
                delivered_at=datetime.now(timezone.utc) - timedelta(days=3),
            )
            db.add(order)
            db.flush()

            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=item_prod.id,
                    quantity=1,
                    unit_price_etb=item_prod.price_etb,
                    line_total_etb=item_prod.price_etb,
                )
            )

        db.commit()
        print("Lijnet database seeded successfully (60% Local Ethiopian / 40% Imported).")
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
