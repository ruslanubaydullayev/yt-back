import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Session, User

ALGORITHM = "HS256"
SESSION_COOKIE = "session-token"
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days


def create_session_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_session_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_session(db: AsyncSession, user_id: str) -> tuple[str, Session]:
    token = create_session_token(user_id)
    session = Session(
        session_token=secrets.token_urlsafe(32),
        user_id=user_id,
        expires=datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE),
    )
    db.add(session)
    await db.commit()
    return token, session


async def get_current_user(
    db: AsyncSession,
    session_token: str | None,
) -> User | None:
    if not session_token:
        return None
    user_id = decode_session_token(session_token)
    if not user_id:
        return None
    return await get_user_by_id(db, user_id)
