"""Growth strategy management routes."""

from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_client_ip
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import generate_csrf_token
from app.models.audit import AuditAction
from app.models.growth_strategy import StrategyStatus, VerificationStatus
from app.models.user import APIKeyType
from app.services.audit import AuditService
from app.services.growth_strategy import GrowthStrategyService
from app.services.rate_limiter import EngagementRateLimiter
from app.services.twitter import TwitterService
from app.services.user import UserService
from app.tasks.growth_tasks import discover_engagement_targets

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def growth_dashboard(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Growth strategies dashboard."""
    from app.main import templates

    growth_service = GrowthStrategyService(db)
    strategies = await growth_service.get_user_strategies(user.id)

    # Get stats for each strategy
    strategy_data = []
    for strategy in strategies:
        analytics = await growth_service.get_strategy_analytics(strategy.id)
        strategy_data.append({
            "strategy": strategy,
            "analytics": analytics,
        })

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "growth/index.html",
        {
            "request": request,
            "user": user,
            "strategies": strategy_data,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/new", response_class=HTMLResponse)
async def new_strategy_page(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Render new growth strategy page."""
    from app.main import templates

    # Check if user has required API keys and Twitter connected
    user_service = UserService(db)
    twitter_service = TwitterService(db)

    deepseek_key = await user_service.get_api_key(user.id, APIKeyType.DEEPSEEK)
    twitter_account = await twitter_service.get_oauth_account(user.id)

    # Get current follower count if Twitter is connected
    current_followers = 0
    if twitter_account:
        access_token = await twitter_service.get_valid_access_token(user.id)
        if access_token:
            try:
                metrics = await twitter_service.get_user_metrics(access_token)
                current_followers = metrics.get("data", {}).get("public_metrics", {}).get("followers_count", 0)
            except Exception:
                pass

    await twitter_service.close()

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "growth/new.html",
        {
            "request": request,
            "user": user,
            "has_deepseek_key": deepseek_key is not None and deepseek_key.is_valid,
            "has_twitter": twitter_account is not None and twitter_account.is_active,
            "current_followers": current_followers,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/preview")
async def preview_strategy(
    request: Request,
    user: CurrentUser,
    prompt: Annotated[str, Form()],
    verification_status: Annotated[str, Form()],
    current_followers: Annotated[int, Form()] = 0,
    db: AsyncSession = Depends(get_db),
):
    """Parse strategy prompt and show preview with estimates."""
    from app.main import templates

    user_service = UserService(db)
    deepseek_key = await user_service.get_decrypted_api_key(user.id, APIKeyType.DEEPSEEK)

    if not deepseek_key:
        return RedirectResponse(
            url="/growth/new?error=Please+configure+your+DeepSeek+API+key+first",
            status_code=status.HTTP_302_FOUND,
        )

    growth_service = GrowthStrategyService(db)

    try:
        # Parse verification status
        verification = VerificationStatus(verification_status)

        # Parse the prompt
        config = await growth_service.parse_strategy_prompt(prompt, deepseek_key)

        # Create a temporary strategy for estimation (not saved)
        temp_strategy = await growth_service.create_strategy(
            user_id=user.id,
            config=config,
            original_prompt=prompt,
            verification_status=verification,
            starting_followers=current_followers,
        )

        # Generate estimates
        estimates = await growth_service.estimate_results(temp_strategy)

        # Generate AI plan
        plan = await growth_service.generate_ai_plan(temp_strategy, deepseek_key)

        await db.commit()

        csrf_token = generate_csrf_token()

        response = templates.TemplateResponse(
            "growth/preview.html",
            {
                "request": request,
                "user": user,
                "strategy": temp_strategy,
                "config": {
                    "name": config.name,
                    "duration_days": config.duration_days,
                    "niche_keywords": config.niche_keywords,
                    "target_accounts": config.target_accounts,
                    "daily_follows": config.daily_follows,
                    "daily_likes": config.daily_likes,
                    "daily_retweets": config.daily_retweets,
                    "daily_replies": config.daily_replies,
                },
                "estimates": estimates,
                "plan": plan,
                "csrf_token": csrf_token,
            },
        )
        response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
        return response

    except Exception as e:
        logger.error("Failed to parse strategy prompt", error=str(e))
        await db.rollback()
        return RedirectResponse(
            url=f"/growth/new?error=Failed+to+parse+prompt:+{str(e)[:50].replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/create")
async def create_strategy(
    request: Request,
    user: CurrentUser,
    strategy_id: Annotated[UUID, Form()],
    db: AsyncSession = Depends(get_db),
):
    """Activate a draft strategy."""
    growth_service = GrowthStrategyService(db)

    strategy = await growth_service.get_strategy(strategy_id, user.id)
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    if strategy.status != StrategyStatus.DRAFT:
        return RedirectResponse(
            url="/growth?error=Strategy+already+activated",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        # Activate the strategy
        await growth_service.activate_strategy(strategy.id)

        # Queue target discovery
        discover_engagement_targets.delay(str(strategy.id))

        # Log audit
        audit_service = AuditService(db)
        await audit_service.log(
            action=AuditAction.GROWTH_STRATEGY_CREATED,
            user_id=user.id,
            resource_type="growth_strategy",
            resource_id=str(strategy.id),
            details={
                "name": strategy.name,
                "duration_days": strategy.duration_days,
                "target_followers": strategy.target_followers,
            },
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info(
            "Growth strategy created and activated",
            strategy_id=str(strategy.id),
            user_id=str(user.id),
        )

        return RedirectResponse(
            url=f"/growth/{strategy.id}?success=Strategy+activated+successfully",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        logger.error("Failed to activate strategy", error=str(e))
        await db.rollback()
        return RedirectResponse(
            url="/growth?error=Failed+to+activate+strategy",
            status_code=status.HTTP_302_FOUND,
        )


@router.get("/{strategy_id}", response_class=HTMLResponse)
async def view_strategy(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """View a growth strategy."""
    from app.main import templates

    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    # Get analytics
    analytics = await growth_service.get_strategy_analytics(strategy_id)

    # Get rate limit usage
    rate_limiter = EngagementRateLimiter(db)
    rate_usage = await rate_limiter.get_usage(user.id)

    # Get pending targets count
    pending_targets = await growth_service.get_pending_targets(strategy_id, limit=100)

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "growth/view.html",
        {
            "request": request,
            "user": user,
            "strategy": strategy,
            "analytics": analytics,
            "rate_usage": rate_usage,
            "pending_count": len(pending_targets),
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/{strategy_id}/analytics", response_class=HTMLResponse)
async def strategy_analytics(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """View detailed analytics for a strategy."""
    from app.main import templates

    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    analytics = await growth_service.get_strategy_analytics(strategy_id)

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "growth/analytics.html",
        {
            "request": request,
            "user": user,
            "strategy": strategy,
            "analytics": analytics,
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/{strategy_id}/targets", response_class=HTMLResponse)
async def strategy_targets(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """View engagement targets for a strategy."""
    from app.main import templates
    from sqlalchemy import select
    from app.models.growth_strategy import EngagementTarget

    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    # Get all targets
    stmt = select(EngagementTarget).where(
        EngagementTarget.strategy_id == strategy_id,
    ).order_by(EngagementTarget.created_at.desc()).limit(100)

    result = await db.execute(stmt)
    targets = list(result.scalars().all())

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "growth/targets.html",
        {
            "request": request,
            "user": user,
            "strategy": strategy,
            "targets": targets,
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/{strategy_id}/logs", response_class=HTMLResponse)
async def strategy_logs(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """View engagement logs for a strategy."""
    from app.main import templates
    from sqlalchemy import select
    from app.models.growth_strategy import EngagementLog

    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    # Get logs
    stmt = select(EngagementLog).where(
        EngagementLog.strategy_id == strategy_id,
    ).order_by(EngagementLog.created_at.desc()).limit(100)

    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "growth/logs.html",
        {
            "request": request,
            "user": user,
            "strategy": strategy,
            "logs": logs,
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/{strategy_id}/pause")
async def pause_strategy(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Pause a strategy."""
    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    try:
        await growth_service.pause_strategy(strategy.id)

        audit_service = AuditService(db)
        await audit_service.log(
            action=AuditAction.GROWTH_STRATEGY_PAUSED,
            user_id=user.id,
            resource_type="growth_strategy",
            resource_id=str(strategy.id),
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info("Strategy paused", strategy_id=str(strategy_id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/growth/{strategy_id}?success=Strategy+paused",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        return RedirectResponse(
            url=f"/growth/{strategy_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{strategy_id}/resume")
async def resume_strategy(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Resume a paused strategy."""
    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    try:
        await growth_service.resume_strategy(strategy.id)

        audit_service = AuditService(db)
        await audit_service.log(
            action=AuditAction.GROWTH_STRATEGY_RESUMED,
            user_id=user.id,
            resource_type="growth_strategy",
            resource_id=str(strategy.id),
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info("Strategy resumed", strategy_id=str(strategy_id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/growth/{strategy_id}?success=Strategy+resumed",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        return RedirectResponse(
            url=f"/growth/{strategy_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{strategy_id}/cancel")
async def cancel_strategy(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a strategy."""
    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    try:
        await growth_service.cancel_strategy(strategy.id)

        audit_service = AuditService(db)
        await audit_service.log(
            action=AuditAction.GROWTH_STRATEGY_CANCELLED,
            user_id=user.id,
            resource_type="growth_strategy",
            resource_id=str(strategy.id),
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info("Strategy cancelled", strategy_id=str(strategy_id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/growth/{strategy_id}?success=Strategy+cancelled",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        return RedirectResponse(
            url=f"/growth/{strategy_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{strategy_id}/delete")
async def delete_strategy(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Delete a strategy."""
    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    strategy.soft_delete()
    await db.commit()

    logger.info("Strategy deleted", strategy_id=str(strategy_id), user_id=str(user.id))

    return RedirectResponse(
        url="/growth?success=Strategy+deleted",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{strategy_id}/refresh-targets")
async def refresh_targets(
    request: Request,
    strategy_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger target discovery."""
    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    # Queue target discovery
    discover_engagement_targets.delay(str(strategy.id))

    return RedirectResponse(
        url=f"/growth/{strategy_id}/targets?success=Refreshing+targets...",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{strategy_id}/approve-reply/{target_id}")
async def approve_reply(
    request: Request,
    strategy_id: UUID,
    target_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Approve an AI-generated reply."""
    from sqlalchemy import select
    from app.models.growth_strategy import EngagementTarget

    growth_service = GrowthStrategyService(db)
    strategy = await growth_service.get_strategy(strategy_id, user.id)

    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    stmt = select(EngagementTarget).where(
        EngagementTarget.id == target_id,
        EngagementTarget.strategy_id == strategy_id,
    )
    result = await db.execute(stmt)
    target = result.scalar_one_or_none()

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found",
        )

    target.approve_reply()
    await db.commit()

    return RedirectResponse(
        url=f"/growth/{strategy_id}/targets?success=Reply+approved",
        status_code=status.HTTP_302_FOUND,
    )
