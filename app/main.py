from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import (
    admin,
    auth_api,
    checkout,
    orders,
    orders_api,
    products_api,
    storefront,
    telegram,
)
from app.services.auth import AdminAuthRequired


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Bilingual (English & Amharic) Children's E-Commerce Platform for Addis Ababa, Ethiopia",
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="lijnet_session",
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
)


@app.exception_handler(AdminAuthRequired)
async def admin_auth_redirect_handler(request: Request, exc: AdminAuthRequired):
    return RedirectResponse(url=f"/admin/login?next={quote(exc.next_url)}", status_code=303)


# Web & Storefront Routers
app.include_router(storefront.router)
app.include_router(checkout.router)
app.include_router(orders.router)
app.include_router(admin.router)
app.include_router(telegram.router)

# Mobile App REST API Routers (/api/v1/*)
app.include_router(auth_api.router)
app.include_router(products_api.router)
app.include_router(orders_api.router)


@app.get("/healthz", tags=["System"])
async def health_check():
    return JSONResponse(
        {
            "status": "ok",
            "app": settings.APP_NAME,
            "legal_entity": settings.LEGAL_NAME,
            "env": settings.APP_ENV,
        }
    )
