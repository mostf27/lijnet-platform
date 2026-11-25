from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserRole
from app.services.auth import authenticate_user, hash_password, normalize_ethiopian_phone
from app.services.i18n import build_cart_summary, get_lang, get_translations, templates

router = APIRouter(tags=["Storefront Authentication"])


def _safe_next_url(next_url: str | None) -> str:
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@router.get("/login", response_class=HTMLResponse)
async def storefront_login_page(
    request: Request,
    next_url: str = Query(default="/", alias="next"),
    error: str | None = Query(default=None),
    msg: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    safe_next = _safe_next_url(next_url)
    if request.session.get("user_id"):
        return RedirectResponse(url=safe_next, status_code=303)

    lang = get_lang(request)
    t = get_translations(lang)
    cart = build_cart_summary(request, db, sub_city=request.session.get("sub_city", "Bole"))

    return templates.TemplateResponse(
        request=request,
        name="auth_login.html",
        context={
            "lang": lang,
            "t": t,
            "cart": cart,
            "next_url": safe_next,
            "error": error,
            "info_msg": msg,
        },
    )


@router.post("/login")
async def storefront_login_submit(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/"),
    db: Session = Depends(get_db),
):
    safe_next = _safe_next_url(next_url)
    user = authenticate_user(db, identifier, password)
    if not user:
        return RedirectResponse(
            url=f"/login?next={quote(safe_next)}&error=Invalid+credentials+or+inactive+account.",
            status_code=303,
        )

    request.session["user_id"] = user.id
    request.session["user_name"] = user.full_name
    request.session["user_role"] = user.role.value
    if safe_next == "/" and user.role == UserRole.VENDOR:
        return RedirectResponse(url="/seller", status_code=303)
    return RedirectResponse(url=safe_next, status_code=303)


@router.get("/register", response_class=HTMLResponse)
async def storefront_register_page(
    request: Request,
    next_url: str = Query(default="/", alias="next"),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    safe_next = _safe_next_url(next_url)
    if request.session.get("user_id"):
        return RedirectResponse(url=safe_next, status_code=303)

    lang = get_lang(request)
    t = get_translations(lang)
    cart = build_cart_summary(request, db, sub_city=request.session.get("sub_city", "Bole"))

    return templates.TemplateResponse(
        request=request,
        name="auth_register.html",
        context={
            "lang": lang,
            "t": t,
            "cart": cart,
            "next_url": safe_next,
            "error": error,
        },
    )


@router.post("/register")
async def storefront_register_submit(
    request: Request,
    account_type: str = Form("BUYER"),
    full_name: str = Form(...),
    email: str = Form(...),
    phone_number: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/"),
    db: Session = Depends(get_db),
):
    safe_next = _safe_next_url(next_url)
    clean_email = email.strip().lower()
    norm_phone = normalize_ethiopian_phone(phone_number)

    if len(password) < 6:
        return RedirectResponse(
            url=f"/register?next={quote(safe_next)}&error=Password+must+be+at+least+6+characters.",
            status_code=303,
        )

    existing = db.scalar(
        select(User).where(or_(User.email == clean_email, User.phone_number == norm_phone))
    )
    if existing:
        return RedirectResponse(
            url=f"/register?next={quote(safe_next)}&error=Email+or+phone+is+already+registered.+Please+sign+in.",
            status_code=303,
        )

    chosen_role = (
        UserRole.VENDOR
        if account_type.strip().upper() in {"PRODUCT_OWNER", "SELLER", "VENDOR"}
        else UserRole.CUSTOMER
    )

    new_user = User(
        full_name=full_name.strip(),
        email=clean_email,
        phone_number=norm_phone,
        password_hash=hash_password(password),
        role=chosen_role,
        is_active=True,
        is_verified=False,
        preferred_lang=get_lang(request),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    request.session["user_id"] = new_user.id
    request.session["user_name"] = new_user.full_name
    request.session["user_role"] = new_user.role.value
    if safe_next == "/" and chosen_role == UserRole.VENDOR:
        return RedirectResponse(url="/seller", status_code=303)
    return RedirectResponse(url=safe_next, status_code=303)


@router.get("/logout")
@router.post("/logout")
async def storefront_logout(request: Request):
    request.session.pop("user_id", None)
    request.session.pop("user_name", None)
    request.session.pop("user_role", None)
    return RedirectResponse(url="/", status_code=303)
