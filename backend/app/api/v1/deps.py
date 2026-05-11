import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db


async def get_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


def require_admin(x_admin_key: str = Header(default="")) -> None:
    """
    Single source of truth for admin endpoint authorization.

    Constant-time compare via :func:`secrets.compare_digest` to avoid
    timing-side-channel leaks of the configured admin key. When the
    server-side key is unset we return 503 (the admin surface is
    disabled) instead of 401, which is the more accurate signal for
    ops tooling probing the endpoint.
    """
    expected = settings.ADMIN_API_KEY or ""
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API not configured",
        )
    provided = x_admin_key or ""
    if not secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
