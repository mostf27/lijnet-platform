from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ADDIS_SUBCITY_FEES
from app.database import get_db
from app.models import AgeRange, Product, ProductCategory
from app.services.i18n import (
    build_cart_summary,
    get_lang,
    get_translations,
    localize_product,
    templates,
)

router = APIRouter(tags=["Storefront"])


@router.get("/set-language/{lang}")
async def set_language(lang: str, request: Request):
    selected = lang if lang in ("en", "am") else "en"
    referer = request.headers.get("referer") or "/"
    response = RedirectResponse(url=referer, status_code=303)
    response.set_cookie(key="lijnet_lang", value=selected, max_age=60 * 60 * 24 * 90, httponly=True)
    return response


@router.get("/", response_class=HTMLResponse)
async def homepage(
    request: Request,
    age: str | None = Query(default=None),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    lang = get_lang(request)
    t = get_translations(lang)

    stmt = select(Product).where(Product.is_active.is_(True))
    if age:
        try:
            stmt = stmt.where(Product.age_range == AgeRange(age))
        except ValueError:
            pass
    if category:
        try:
            stmt = stmt.where(Product.category == ProductCategory(category))
        except ValueError:
            pass
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            Product.title_en.ilike(pattern)
            | Product.title_am.ilike(pattern)
            | Product.description_en.ilike(pattern)
            | Product.description_am.ilike(pattern)
        )

    products = db.scalars(stmt.order_by(Product.created_at.desc())).all()
    bundles = db.scalars(
        select(Product).where(Product.is_active.is_(True), Product.is_starter_bundle.is_(True))
    ).all()

    sub_city = request.session.get("sub_city", "Bole")
    cart = build_cart_summary(request, db, sub_city=sub_city)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "lang": lang,
            "t": t,
            "products": [localize_product(p, lang) for p in products],
            "bundles": [localize_product(b, lang) for b in bundles],
            "active_age": age,
            "active_category": category,
            "search_query": q or "",
            "cart": cart,
            "age_ranges": [a.value for a in AgeRange],
            "categories": [c.value for c in ProductCategory],
        },
    )


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    lang = get_lang(request)
    t = get_translations(lang)

    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.is_active.is_(True))
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    sub_city = request.session.get("sub_city", "Bole")
    cart = build_cart_summary(request, db, sub_city=sub_city)

    return templates.TemplateResponse(
        request=request,
        name="product_detail.html",
        context={
            "lang": lang,
            "t": t,
            "product": localize_product(product, lang),
            "cart": cart,
        },
    )


@router.post("/cart/add")
async def add_to_cart(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(1),
    db: Session = Depends(get_db),
):
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.is_active.is_(True))
    )
    if not product or product.stock_count <= 0:
        raise HTTPException(status_code=400, detail="Product is unavailable or out of stock.")

    cart: dict[str, int] = dict(request.session.get("cart", {}))
    key = str(product_id)
    new_qty = min(cart.get(key, 0) + max(quantity, 1), product.stock_count)
    cart[key] = new_qty
    request.session["cart"] = cart

    if request.headers.get("HX-Request"):
        total_items = sum(cart.values())
        return HTMLResponse(
            f'<span id="cart-badge" class="ml-1 inline-flex items-center justify-center rounded-full '
            f'bg-[#E06D53] px-2 py-0.5 text-xs font-bold text-white">{total_items}</span>'
        )

    return RedirectResponse(url="/checkout", status_code=303)


@router.post("/cart/update")
async def update_cart_item(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(...),
):
    cart: dict[str, int] = dict(request.session.get("cart", {}))
    key = str(product_id)
    if quantity <= 0:
        cart.pop(key, None)
    else:
        cart[key] = quantity
    request.session["cart"] = cart
    return RedirectResponse(url="/checkout", status_code=303)


@router.get("/cart/estimate", response_class=HTMLResponse)
async def estimate_delivery_fee_htmx(
    request: Request,
    sub_city: str = Query("Bole"),
    db: Session = Depends(get_db),
):
    if sub_city in ADDIS_SUBCITY_FEES:
        request.session["sub_city"] = sub_city
    lang = get_lang(request)
    t = get_translations(lang)
    cart = build_cart_summary(request, db, sub_city=sub_city)

    fee_text = (
        t["free_delivery"]
        if cart["free_delivery_applied"]
        else f"{cart['delivery_fee_etb']:,.2f} {t['etb']}"
    )
    return HTMLResponse(
        f"""
        <div id="order-totals" class="space-y-2 border-t pt-4 text-sm">
          <div class="flex justify-between"><span>{t["subtotal"]}</span><span class="font-medium">{cart["subtotal_etb"]:,.2f} {t["etb"]}</span></div>
          <div class="flex justify-between"><span>{t["delivery_fee"]} ({sub_city})</span><span class="font-medium text-[#16697A]">{fee_text}</span></div>
          <div class="flex justify-between border-t pt-2 text-base font-bold text-[#1F2933]"><span>{t["total"]}</span><span>{cart["total_etb"]:,.2f} {t["etb"]}</span></div>
        </div>
        """
    )
