from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.services.auth import require_admin_session
from app.services.telegram_bot import TelegramBotClient, process_telegram_update

router = APIRouter(prefix="/telegram", tags=["Telegram Bot (@lijnet_et)"])


@router.post("/webhook")
async def telegram_webhook_handler(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Receives real-time Telegram Bot updates with secret-token header verification."""
    if (
        settings.TELEGRAM_WEBHOOK_SECRET
        and x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram webhook secret token.",
        )

    update = await request.json()
    await process_telegram_update(update, db)
    return JSONResponse({"ok": True})


@router.post("/register-webhook")
async def register_telegram_webhook(
    admin_user: User = Depends(require_admin_session),
):
    """Admin-only endpoint to register {BASE_URL}/telegram/webhook with Telegram."""
    bot = TelegramBotClient()
    if not bot.is_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TELEGRAM_BOT_TOKEN is not configured in .env.",
        )
    webhook_url = f"{settings.BASE_URL.rstrip('/')}/telegram/webhook"
    result = await bot.set_webhook(webhook_url)
    return JSONResponse({"webhook_url": webhook_url, "telegram_response": result})
