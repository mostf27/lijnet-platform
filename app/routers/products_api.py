from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ADDIS_SUBCITY_FEES, settings
from app.database import get_db
from app.models import AgeRange, OriginType, Product, ProductCategory, User
from app.services.auth import get_current_api_user
from app.services.i18n import localize_product

router = APIRouter(prefix="/api/v1/products", tags=["Mobile API Products"])


class MobileProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    title: str
    title_en: str
    title_am: str
    description: str
    description_en: str
    description_am: str
    category: str
    age_range: str
    origin_type: str
    is_local: bool
    safety_certified: bool
    is_hygiene_sealed: bool
    is_starter_bundle: bool
    price_etb: Decimal
    stock_count: int
    image_url: str


class MobileProductDetailOut(MobileProductOut):
    statutory_return_days: int
    return_policy_en: str
    return_policy_am: str


class DeliveryConfigOut(BaseModel):
    sub_city_fees_etb: dict[str, Decimal]
    free_delivery_threshold_etb: Decimal
    cod_max_limit_etb: Decimal


def _to_mobile_product(product: Product, lang: str) -> dict[str, Any]:
    data = localize_product(product, lang)
    data["description_en"] = product.description_en
    data["description_am"] = product.description_am
    return data


@router.get("", response_model=list[MobileProductOut])
async def list_mobile_products(
    lang: str = Query(default="en", pattern="^(en|am)$"),
    age_range: AgeRange | None = Query(default=None),
    category: ProductCategory | None = Query(default=None),
    origin_type: OriginType | None = Query(default=None),
    starter_bundles_only: bool = Query(default=False),
    q: str | None = Query(default=None, max_length=120),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user),
):
    """Lists active children's products with bilingual localization and developmental filters."""
    stmt = select(Product).where(Product.is_active.is_(True))

    if age_range is not None:
        stmt = stmt.where(Product.age_range == age_range)
    if category is not None:
        stmt = stmt.where(Product.category == category)
    if origin_type is not None:
        stmt = stmt.where(Product.origin_type == origin_type)
    if starter_bundles_only:
        stmt = stmt.where(Product.is_starter_bundle.is_(True))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            Product.title_en.ilike(pattern)
            | Product.title_am.ilike(pattern)
            | Product.description_en.ilike(pattern)
            | Product.description_am.ilike(pattern)
            | Product.sku.ilike(pattern)
        )

    products = db.scalars(stmt.order_by(Product.created_at.desc()).offset(skip).limit(limit)).all()

    return [MobileProductOut(**_to_mobile_product(p, lang)) for p in products]


@router.get("/delivery-config", response_model=DeliveryConfigOut)
async def get_mobile_delivery_config(
    current_user: User = Depends(get_current_api_user),
):
    """Returns Addis Ababa sub-city fees, free-delivery threshold, and COD limit."""
    return DeliveryConfigOut(
        sub_city_fees_etb=ADDIS_SUBCITY_FEES,
        free_delivery_threshold_etb=settings.FREE_DELIVERY_THRESHOLD_ETB,
        cod_max_limit_etb=settings.COD_MAX_LIMIT_ETB,
    )


@router.get("/{product_id}", response_model=MobileProductDetailOut)
async def get_mobile_product_detail(
    product_id: int,
    lang: str = Query(default="en", pattern="^(en|am)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user),
):
    """Returns full bilingual product specifications, safety flags, and statutory return disclosure."""
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.is_active.is_(True))
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        )

    payload = _to_mobile_product(product, lang)
    return MobileProductDetailOut(
        **payload,
        statutory_return_days=settings.STATUTORY_RETURN_DAYS,
        return_policy_en=(
            "Unopened, non-hygiene items may be returned or exchanged within 7 calendar days "
            "of delivery under Ethiopia's Electronic Transaction Proclamation No. 1205/2020."
        ),
        return_policy_am=(
            "ያልተከፈቱ ምርቶች በኢትዮጵያ የኤሌክትሮኒክስ ግብይት አዋጅ ቁጥር 1205/2020 መሠረት "
            "ከደረሱበት ቀን ጀምሮ በ7 ቀናት ውስጥ መመለስ ወይም መቀየር ይችላሉ።"
        ),
    )
