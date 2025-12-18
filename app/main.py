"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import api_router
from app.auth.dependencies import get_optional_user
from app.core.config import settings
from app.core.database import close_db, get_db, init_db
from app.core.logging import get_logger, setup_logging
from app.middleware import TokenRefreshMiddleware

# Setup logging
setup_logging()
logger = get_logger(__name__)

# Templates configuration
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting application", app_name=settings.app_name)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    yield

    # Cleanup
    await close_db()
    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    description="Enterprise-grade Tweet Scheduling SaaS",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# Add middleware for automatic token refresh
app.add_middleware(TokenRefreshMiddleware)

# Mount static files
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

# Include API routes
app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page - redirect to dashboard if logged in, otherwise to login."""
    from sqlalchemy.ext.asyncio import AsyncSession

    # Get optional user
    db_gen = get_db()
    db: AsyncSession = await db_gen.__anext__()

    try:
        user = await get_optional_user(request, db)

        if user:
            return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

        return templates.TemplateResponse(
            "home.html",
            {"request": request},
        )
    finally:
        await db_gen.aclose()


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Custom 404 handler."""
    return templates.TemplateResponse(
        "errors/404.html",
        {"request": request},
        status_code=status.HTTP_404_NOT_FOUND,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    """Custom 500 handler."""
    logger.error("Internal server error", error=str(exc))
    return templates.TemplateResponse(
        "errors/500.html",
        {"request": request},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions - redirect 401 to login for HTML pages."""
    if exc.status_code == 401:
        # Check if this is an HTML request (not API/JSON)
        accept = request.headers.get("accept", "")
        if "text/html" in accept or not accept.startswith("application/json"):
            return RedirectResponse(
                url="/login?error=Please+log+in+to+continue",
                status_code=status.HTTP_302_FOUND,
            )

    # For API requests or other errors, return JSON
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
