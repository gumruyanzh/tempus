"""Authentication routes."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    OptionalUser,
    get_client_ip,
    get_user_agent,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import generate_csrf_token
from app.services.audit import AuditService
from app.services.auth import AuthError, AuthService
from app.services.twitter import TwitterService

logger = get_logger(__name__)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    user: OptionalUser,
):
    """Render login page."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    csrf_token = generate_csrf_token()

    from app.main import templates

    response = templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
):
    """Handle login form submission."""
    auth_service = AuthService(db)
    audit_service = AuditService(db)

    user = await auth_service.authenticate_user(email, password)

    if not user:
        # Log failed attempt
        existing_user = await auth_service.get_user_by_email(email)
        if existing_user:
            await audit_service.log_login(
                user_id=existing_user.id,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                success=False,
                error_message="Invalid password",
            )
            await db.commit()

        return RedirectResponse(
            url="/login?error=Invalid+email+or+password",
            status_code=status.HTTP_302_FOUND,
        )

    # Create tokens
    tokens = auth_service.create_tokens(user)

    # Log successful login
    await audit_service.log_login(
        user_id=user.id,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        success=True,
    )
    await db.commit()

    # Set cookies and redirect
    # Use samesite="lax" to allow OAuth redirects while maintaining security
    redirect = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key="access_token",
        value=tokens["access_token"],
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.jwt_access_token_expire_minutes * 60,
        path="/",
    )
    redirect.set_cookie(
        key="refresh_token",
        value=tokens["refresh_token"],
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        path="/",
    )

    logger.info("User logged in", user_id=str(user.id))
    return redirect


@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    user: OptionalUser,
):
    """Render registration page."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    csrf_token = generate_csrf_token()

    from app.main import templates

    response = templates.TemplateResponse(
        "auth/register.html",
        {
            "request": request,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/register")
async def register(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
    full_name: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle registration form submission."""
    # Validate passwords match
    if password != confirm_password:
        return RedirectResponse(
            url="/register?error=Passwords+do+not+match",
            status_code=status.HTTP_302_FOUND,
        )

    auth_service = AuthService(db)
    audit_service = AuditService(db)

    try:
        user = await auth_service.register_user(
            email=email,
            password=password,
            full_name=full_name,
        )

        # Log registration
        await audit_service.log_registration(
            user_id=user.id,
            email=email,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
        await db.commit()

        # Create tokens and log in
        tokens = auth_service.create_tokens(user)

        redirect = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
        redirect.set_cookie(
            key="access_token",
            value=tokens["access_token"],
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=settings.jwt_access_token_expire_minutes * 60,
            path="/",
        )
        redirect.set_cookie(
            key="refresh_token",
            value=tokens["refresh_token"],
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
            path="/",
        )

        logger.info("User registered", user_id=str(user.id))
        return redirect

    except AuthError as e:
        return RedirectResponse(
            url=f"/register?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.get("/logout")
async def logout(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Handle logout."""
    audit_service = AuditService(db)

    await audit_service.log_logout(
        user_id=user.id,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    await db.commit()

    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")

    logger.info("User logged out", user_id=str(user.id))
    return response


# Twitter OAuth Routes - Sign In / Sign Up


@router.get("/twitter/signin")
async def twitter_signin(
    request: Request,
    user: OptionalUser,
    db: AsyncSession = Depends(get_db),
):
    """Initiate Twitter OAuth flow for sign in/sign up."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    twitter_service = TwitterService(db)
    auth_url, state_verifier = twitter_service.get_authorization_url()

    logger.info(
        "Twitter OAuth signin initiated",
        auth_url=auth_url[:100] + "...",
        redirect_uri=settings.twitter_redirect_uri,
    )

    # Store state and verifier in cookie with auth mode
    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="twitter_oauth_state",
        value=f"signin:{state_verifier}",  # Prefix with mode
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=600,  # 10 minutes
        path="/",  # Explicit path for cookie
    )

    return response


@router.get("/twitter/connect")
async def twitter_connect(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Initiate Twitter OAuth flow for connecting account."""
    twitter_service = TwitterService(db)
    auth_url, state_verifier = twitter_service.get_authorization_url()

    # Store state and verifier in cookie with connect mode
    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="twitter_oauth_state",
        value=f"connect:{state_verifier}",  # Prefix with mode
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=600,  # 10 minutes
        path="/",  # Explicit path for cookie
    )

    return response


@router.get("/twitter/callback/test")
async def twitter_callback_test(request: Request):
    """Test endpoint to verify callback URL is reachable."""
    return {"status": "ok", "message": "Callback URL is reachable", "url": str(request.url)}


@router.get("/twitter/callback")
async def twitter_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle Twitter OAuth callback for both sign-in and connect flows."""
    # Log ALL query parameters for debugging
    logger.info(
        "Twitter OAuth callback received",
        full_url=str(request.url),
        query_params=dict(request.query_params),
        has_code=bool(code),
        has_state=bool(state),
        error=error,
        error_description=error_description,
        cookies=list(request.cookies.keys()),
    )

    # Handle OAuth errors from Twitter
    if error:
        logger.warning(
            "Twitter OAuth error",
            error=error,
            error_description=error_description,
        )
        return RedirectResponse(
            url=f"/login?error=Twitter+authorization+failed:+{error}",
            status_code=status.HTTP_302_FOUND,
        )

    # Validate required parameters
    if not code or not state:
        logger.warning("Twitter OAuth callback missing code or state")
        return RedirectResponse(
            url="/login?error=Invalid+OAuth+response",
            status_code=status.HTTP_302_FOUND,
        )

    # Get stored state and verifier
    stored_cookie = request.cookies.get("twitter_oauth_state")

    if not stored_cookie:
        logger.warning("Twitter OAuth callback: No state cookie found")
        return RedirectResponse(
            url="/login?error=OAuth+state+expired",
            status_code=status.HTTP_302_FOUND,
        )

    # Parse stored value (mode:state:verifier)
    try:
        mode, stored_state, code_verifier = stored_cookie.split(":", 2)
    except ValueError:
        return RedirectResponse(
            url="/login?error=Invalid+OAuth+state",
            status_code=status.HTTP_302_FOUND,
        )

    # Verify state
    if state != stored_state:
        return RedirectResponse(
            url="/login?error=OAuth+state+mismatch",
            status_code=status.HTTP_302_FOUND,
        )

    twitter_service = TwitterService(db)
    audit_service = AuditService(db)

    try:
        # Exchange code for tokens
        token_data = await twitter_service.exchange_code_for_tokens(code, code_verifier)

        # Get Twitter user info
        user_data = await twitter_service.get_current_user(token_data["access_token"])

        if mode == "signin":
            # Sign in or sign up flow
            user, is_new = await twitter_service.sign_in_or_sign_up_with_twitter(
                token_data=token_data,
                user_data=user_data,
            )

            # Log audit
            if is_new:
                await audit_service.log_registration(
                    user_id=user.id,
                    email=f"@{user_data.get('data', {}).get('username', 'unknown')}",
                    ip_address=get_client_ip(request),
                    user_agent=get_user_agent(request),
                )
            else:
                await audit_service.log_login(
                    user_id=user.id,
                    ip_address=get_client_ip(request),
                    user_agent=get_user_agent(request),
                    success=True,
                )
            await db.commit()

            # Create JWT tokens
            from app.services.auth import AuthService
            auth_service = AuthService(db)
            tokens = auth_service.create_tokens(user)

            # Set cookies and redirect
            redirect = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
            redirect.set_cookie(
                key="access_token",
                value=tokens["access_token"],
                httponly=True,
                secure=settings.is_production,
                samesite="lax",
                max_age=settings.jwt_access_token_expire_minutes * 60,
                path="/",
            )
            redirect.set_cookie(
                key="refresh_token",
                value=tokens["refresh_token"],
                httponly=True,
                secure=settings.is_production,
                samesite="lax",
                max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
                path="/",
            )
            redirect.delete_cookie("twitter_oauth_state", path="/")

            logger.info(
                "User signed in via Twitter",
                user_id=str(user.id),
                is_new=is_new,
            )
            return redirect

        else:
            # Connect flow - requires existing authenticated user
            from app.auth.dependencies import get_current_user
            try:
                user = await get_current_user(request, db)
            except HTTPException:
                return RedirectResponse(
                    url="/login?error=Please+sign+in+first",
                    status_code=status.HTTP_302_FOUND,
                )

            # Save OAuth account
            oauth_account = await twitter_service.save_oauth_account(
                user_id=user.id,
                token_data=token_data,
                user_data=user_data,
            )

            # Log audit
            await audit_service.log_twitter_connected(
                user_id=user.id,
                twitter_username=oauth_account.provider_username or "unknown",
                ip_address=get_client_ip(request),
            )
            await db.commit()

            logger.info(
                "Twitter account connected",
                user_id=str(user.id),
                twitter_username=oauth_account.provider_username,
            )

            response = RedirectResponse(
                url="/settings?success=Twitter+account+connected",
                status_code=status.HTTP_302_FOUND,
            )
            response.delete_cookie("twitter_oauth_state", path="/")
            return response

    except Exception as e:
        logger.error("Twitter OAuth callback failed", error=str(e))
        error_url = "/login" if mode == "signin" else "/settings"
        return RedirectResponse(
            url=f"{error_url}?error=Twitter+authentication+failed",
            status_code=status.HTTP_302_FOUND,
        )
    finally:
        await twitter_service.close()


@router.post("/twitter/disconnect")
async def twitter_disconnect(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Twitter account."""
    twitter_service = TwitterService(db)
    audit_service = AuditService(db)

    success = await twitter_service.disconnect_account(user.id)

    if success:
        await audit_service.log_twitter_disconnected(
            user_id=user.id,
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info("Twitter account disconnected", user_id=str(user.id))

    return RedirectResponse(
        url="/settings?success=Twitter+account+disconnected",
        status_code=status.HTTP_302_FOUND,
    )
