"""Token refresh middleware for automatic session renewal."""

from datetime import timedelta

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import create_access_token, decode_token

logger = get_logger(__name__)


class TokenRefreshMiddleware(BaseHTTPMiddleware):
    """Middleware that automatically refreshes expired access tokens using refresh tokens."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Only process if we have both tokens
        access_token = request.cookies.get("access_token")
        refresh_token = request.cookies.get("refresh_token")

        if not access_token or not refresh_token:
            return response

        # Check if access token is expired
        access_payload = decode_token(access_token)

        if access_payload is not None:
            # Access token is still valid, no refresh needed
            return response

        # Access token is expired, try to refresh using refresh token
        refresh_payload = decode_token(refresh_token)

        if refresh_payload is None:
            # Refresh token is also expired/invalid, user needs to log in again
            return response

        if refresh_payload.get("type") != "refresh":
            return response

        user_id = refresh_payload.get("sub")
        if not user_id:
            return response

        # Create new access token
        new_access_token = create_access_token(
            data={"sub": user_id},
            expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
        )

        # Set the new access token cookie on the response
        # Use samesite="lax" to allow OAuth redirects while maintaining security
        response.set_cookie(
            key="access_token",
            value=new_access_token,
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=settings.jwt_access_token_expire_minutes * 60,
            path="/",
        )

        logger.debug("Access token refreshed", user_id=user_id)

        return response
