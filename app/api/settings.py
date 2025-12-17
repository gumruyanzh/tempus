"""User settings routes."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_client_ip
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import generate_csrf_token
from app.models.tweet import TweetTone
from app.models.user import APIKeyType
from app.services.audit import AuditService
from app.services.auth import AuthError, AuthService
from app.services.deepseek import DeepSeekService
from app.services.twitter import TwitterService
from app.services.user import UserService

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Render settings page."""
    from app.main import templates

    user_service = UserService(db)
    twitter_service = TwitterService(db)

    # Get API key info
    deepseek_key = await user_service.get_api_key(user.id, APIKeyType.DEEPSEEK)

    # Get Twitter account
    twitter_account = await twitter_service.get_oauth_account(user.id)

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "settings/index.html",
        {
            "request": request,
            "user": user,
            "deepseek_key": deepseek_key,
            "twitter_account": twitter_account,
            "tones": [t.value for t in TweetTone],
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/profile")
async def update_profile(
    request: Request,
    user: CurrentUser,
    full_name: Annotated[Optional[str], Form()] = None,
    timezone: Annotated[str, Form()] = "UTC",
    db: AsyncSession = Depends(get_db),
):
    """Update user profile."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    await user_service.update_profile(
        user=user,
        full_name=full_name,
        timezone_str=timezone,
    )

    await audit_service.log_settings_updated(
        user_id=user.id,
        changes={"full_name": full_name, "timezone": timezone},
        ip_address=get_client_ip(request),
    )
    await db.commit()

    logger.info("Profile updated", user_id=str(user.id))

    return RedirectResponse(
        url="/settings?success=Profile+updated",
        status_code=302,
    )


@router.post("/password")
async def change_password(
    request: Request,
    user: CurrentUser,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
):
    """Change user password."""
    if new_password != confirm_password:
        return RedirectResponse(
            url="/settings?error=Passwords+do+not+match",
            status_code=302,
        )

    auth_service = AuthService(db)
    audit_service = AuditService(db)

    try:
        await auth_service.change_password(
            user=user,
            current_password=current_password,
            new_password=new_password,
        )

        await audit_service.log(
            action=audit_service.log.__self__.__class__.__bases__[0],
            user_id=user.id,
            resource_type="user",
            details={"action": "password_changed"},
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info("Password changed", user_id=str(user.id))

        return RedirectResponse(
            url="/settings?success=Password+changed",
            status_code=302,
        )

    except AuthError as e:
        return RedirectResponse(
            url=f"/settings?error={str(e).replace(' ', '+')}",
            status_code=302,
        )


@router.post("/deepseek-key")
async def update_deepseek_key(
    request: Request,
    user: CurrentUser,
    api_key: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
):
    """Update DeepSeek API key."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    # Validate the API key
    deepseek_service = DeepSeekService(api_key)
    try:
        is_valid = await deepseek_service.validate_api_key()
        if not is_valid:
            return RedirectResponse(
                url="/settings?error=Invalid+DeepSeek+API+key",
                status_code=302,
            )
    except Exception as e:
        logger.error("API key validation failed", error=str(e))
        return RedirectResponse(
            url="/settings?error=Failed+to+validate+API+key",
            status_code=302,
        )
    finally:
        await deepseek_service.close()

    # Check if key already exists
    existing_key = await user_service.get_api_key(user.id, APIKeyType.DEEPSEEK)

    # Store the key
    await user_service.store_api_key(
        user=user,
        key_type=APIKeyType.DEEPSEEK,
        api_key=api_key,
    )

    # Log audit
    action = "rotated" if existing_key else "created"
    if existing_key:
        await audit_service.log_api_key_rotated(
            user_id=user.id,
            key_type="deepseek",
            ip_address=get_client_ip(request),
        )
    else:
        await audit_service.log_api_key_created(
            user_id=user.id,
            key_type="deepseek",
            ip_address=get_client_ip(request),
        )
    await db.commit()

    logger.info(
        f"DeepSeek API key {action}",
        user_id=str(user.id),
    )

    return RedirectResponse(
        url=f"/settings?success=DeepSeek+API+key+{action}",
        status_code=302,
    )


@router.post("/deepseek-key/delete")
async def delete_deepseek_key(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Delete DeepSeek API key."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    success = await user_service.delete_api_key(user.id, APIKeyType.DEEPSEEK)

    if success:
        await audit_service.log(
            action=audit_service.log.__self__.__class__.__bases__[0],
            user_id=user.id,
            resource_type="api_key",
            details={"key_type": "deepseek", "action": "deleted"},
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info("DeepSeek API key deleted", user_id=str(user.id))

    return RedirectResponse(
        url="/settings?success=API+key+deleted",
        status_code=302,
    )


@router.post("/prompt-defaults")
async def update_prompt_defaults(
    request: Request,
    user: CurrentUser,
    default_tone: Annotated[str, Form()],
    default_prompt: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Update default prompt settings."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    await user_service.update_default_prompt_settings(
        user=user,
        default_prompt_template=default_prompt,
        default_tone=default_tone,
    )

    await audit_service.log_settings_updated(
        user_id=user.id,
        changes={"default_tone": default_tone, "default_prompt": "updated"},
        ip_address=get_client_ip(request),
    )
    await db.commit()

    logger.info("Prompt defaults updated", user_id=str(user.id))

    return RedirectResponse(
        url="/settings?success=Prompt+settings+updated",
        status_code=302,
    )
