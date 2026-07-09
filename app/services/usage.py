import logging

import resend

from app.config import settings

logger = logging.getLogger(__name__)


async def check_usage(user) -> dict:
    return {
        "isAuthenticated": user is not None,
        "reason": "ok",
    }


def send_welcome_email(to: str, user_name: str) -> None:
    if not settings.resend_api_key:
        return
    resend.api_key = settings.resend_api_key
    try:
        resend.Emails.send({
            "from": "Ranking Shorts <onboarding@resend.dev>",
            "to": [to],
            "subject": "Welcome to Ranking Shorts!",
            "html": f"<p>Hi {user_name},</p><p>Welcome to Ranking Shorts! Start creating ranked videos today.</p>",
        })
    except Exception:
        logger.exception("Failed to send welcome email to %s", to)
