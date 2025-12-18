"""Middleware package."""

from app.middleware.token_refresh import TokenRefreshMiddleware

__all__ = ["TokenRefreshMiddleware"]
