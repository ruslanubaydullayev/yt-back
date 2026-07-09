from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.services.auth import SESSION_COOKIE, get_current_user

UNPROTECTED_PREFIXES = (
    "/api/auth",
    "/api/webhooks",
    "/api/clips",
    "/api/usage",
    "/api/render",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/health",
    "/static",
)


async def optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_token: str | None = Cookie(None, alias=SESSION_COOKIE),
) -> User | None:
    return await get_current_user(db, session_token)


async def require_user(
    user: User | None = Depends(optional_user),
) -> User:
    if not user:
        raise HTTPException(status_code=403, detail="Unauthenticated")
    return user


def get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
