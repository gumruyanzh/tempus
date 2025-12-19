"""API routes module."""

from fastapi import APIRouter

from app.api import admin, auth, campaigns, dashboard, generate, growth, health, settings, tweets

api_router = APIRouter()

# Include all route modules
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(tweets.router, prefix="/tweets", tags=["tweets"])
api_router.include_router(generate.router, prefix="/generate", tags=["generate"])
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(growth.router, prefix="/growth", tags=["growth"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
