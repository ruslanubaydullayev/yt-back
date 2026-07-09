import secrets
from datetime import datetime, timezone

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import optional_user
from app.models import Account, User
from app.schemas import SessionResponse, SessionUser
from app.services.auth import SESSION_COOKIE, SESSION_MAX_AGE, create_session_token
from app.services.usage import send_welcome_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/signin")
async def signin(request: Request):
    return await signin_google(request)


@router.get("/signin/google")
async def signin_google(request: Request):
    redirect_uri = f"{settings.site_url}/api/auth/callback/google"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback/google")
async def callback_google(request: Request, db: AsyncSession = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    if not user_info:
        raise HTTPException(status_code=400, detail="Failed to get user info")

    email = user_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not provided")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    is_new = user is None

    if not user:
        user = User(
            id=secrets.token_hex(12),
            name=user_info.get("name"),
            email=email,
            email_verified=datetime.now(timezone.utc),
            image=user_info.get("picture"),
        )
        db.add(user)
        await db.flush()

    result = await db.execute(
        select(Account).where(
            Account.provider == "google",
            Account.provider_account_id == user_info["sub"],
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        account = Account(
            user_id=user.id,
            type="oauth",
            provider="google",
            provider_account_id=user_info["sub"],
            access_token=token.get("access_token"),
            refresh_token=token.get("refresh_token"),
            token_type=token.get("token_type"),
            scope=token.get("scope"),
            id_token=token.get("id_token"),
        )
        db.add(account)

    await db.commit()

    if is_new and user.name:
        send_welcome_email(user.email, user.name)

    jwt_token = create_session_token(user.id)
    response = RedirectResponse(url=f"{settings.frontend_url}/dashboard?signInCallback=true")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=jwt_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.site_url.startswith("https"),
    )
    return response


@router.get("/session", response_model=SessionResponse)
async def get_session(user: User | None = Depends(optional_user)):
    if not user:
        return SessionResponse(user=None)
    return SessionResponse(
        user=SessionUser(
            id=user.id,
            name=user.name,
            email=user.email,
            image=user.image,
        ),
        expires=(datetime.now(timezone.utc).isoformat()),
    )


@router.post("/signout")
async def signout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"url": settings.frontend_url}
