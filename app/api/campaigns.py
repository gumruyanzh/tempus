"""Campaign management routes."""

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
from app.models.campaign import CampaignStatus
from app.models.tweet import TweetStatus
from app.models.user import APIKeyType
from app.services.audit import AuditService
from app.services.campaign import CampaignService, CampaignServiceError
from app.services.deepseek import DeepSeekService
from app.services.user import UserService

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def campaigns_list(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """List user's campaigns."""
    from app.main import templates

    campaign_service = CampaignService(db)
    campaigns = await campaign_service.get_user_campaigns(user.id)

    # Get stats for each campaign
    campaign_data = []
    for campaign in campaigns:
        stats = await campaign_service.get_campaign_stats(campaign.id)
        campaign_data.append({
            "campaign": campaign,
            "stats": stats,
        })

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "campaigns/index.html",
        {
            "request": request,
            "user": user,
            "campaigns": campaign_data,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/new", response_class=HTMLResponse)
async def new_campaign_page(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Render new campaign page."""
    from app.main import templates

    # Check if user has required API keys
    user_service = UserService(db)
    deepseek_key = await user_service.get_api_key(user.id, APIKeyType.DEEPSEEK)
    tavily_key = await user_service.get_api_key(user.id, APIKeyType.TAVILY)

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "campaigns/new.html",
        {
            "request": request,
            "user": user,
            "has_deepseek_key": deepseek_key is not None and deepseek_key.is_valid,
            "has_tavily_key": tavily_key is not None and tavily_key.is_valid,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/preview")
async def preview_campaign(
    request: Request,
    user: CurrentUser,
    prompt: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
):
    """Parse campaign prompt and show preview."""
    from app.main import templates

    user_service = UserService(db)
    deepseek_key = await user_service.get_decrypted_api_key(user.id, APIKeyType.DEEPSEEK)

    if not deepseek_key:
        return RedirectResponse(
            url="/campaigns/new?error=Please+configure+your+DeepSeek+API+key+first",
            status_code=status.HTTP_302_FOUND,
        )

    campaign_service = CampaignService(db)
    deepseek_service = DeepSeekService(deepseek_key)

    try:
        config = await campaign_service.parse_campaign_prompt(prompt, deepseek_service)

        csrf_token = generate_csrf_token()

        response = templates.TemplateResponse(
            "campaigns/preview.html",
            {
                "request": request,
                "user": user,
                "prompt": prompt,
                "config": {
                    "topic": config.topic,
                    "frequency_per_day": config.frequency_per_day,
                    "duration_days": config.duration_days,
                    "total_tweets": config.frequency_per_day * config.duration_days,
                    "tone": config.tone.value,
                    "search_keywords": config.search_keywords or [],
                    "custom_instructions": config.custom_instructions,
                },
                "csrf_token": csrf_token,
            },
        )
        response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
        return response

    except Exception as e:
        logger.error("Failed to parse campaign prompt", error=str(e))
        return RedirectResponse(
            url=f"/campaigns/new?error=Failed+to+parse+prompt:+{str(e)[:50].replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )
    finally:
        await deepseek_service.close()


@router.post("/create")
async def create_campaign(
    request: Request,
    user: CurrentUser,
    prompt: Annotated[str, Form()],
    topic: Annotated[str, Form()],
    frequency_per_day: Annotated[int, Form()],
    duration_days: Annotated[int, Form()],
    tone: Annotated[str, Form()],
    posting_start_hour: Annotated[int, Form()] = 9,
    posting_end_hour: Annotated[int, Form()] = 21,
    search_keywords: Annotated[Optional[str], Form()] = None,
    custom_instructions: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Create a new campaign."""
    from app.models.tweet import TweetTone
    from app.services.campaign import CampaignConfig

    user_service = UserService(db)
    deepseek_key = await user_service.get_decrypted_api_key(user.id, APIKeyType.DEEPSEEK)

    if not deepseek_key:
        return RedirectResponse(
            url="/campaigns/new?error=Please+configure+your+DeepSeek+API+key+first",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        # Map tone string to enum
        tone_map = {
            "professional": TweetTone.PROFESSIONAL,
            "casual": TweetTone.CASUAL,
            "viral": TweetTone.VIRAL,
            "thought_leadership": TweetTone.THOUGHT_LEADERSHIP,
        }
        tone_enum = tone_map.get(tone.lower(), TweetTone.PROFESSIONAL)

        # Parse search keywords
        keywords_list = None
        if search_keywords:
            keywords_list = [k.strip() for k in search_keywords.split(",") if k.strip()]

        # Create config
        config = CampaignConfig(
            topic=topic,
            frequency_per_day=max(1, min(10, frequency_per_day)),
            duration_days=max(1, min(90, duration_days)),
            tone=tone_enum,
            search_keywords=keywords_list,
            custom_instructions=custom_instructions if custom_instructions else None,
        )

        campaign_service = CampaignService(db)
        campaign = await campaign_service.create_campaign(
            user_id=user.id,
            config=config,
            user_timezone=user.timezone,
            posting_start_hour=posting_start_hour,
            posting_end_hour=posting_end_hour,
        )

        # Log audit
        audit_service = AuditService(db)
        from app.models.audit import AuditAction
        await audit_service.log(
            action=AuditAction.CAMPAIGN_CREATED,
            user_id=user.id,
            resource_type="campaign",
            resource_id=str(campaign.id),
            details={
                "topic": topic,
                "total_tweets": campaign.total_tweets,
            },
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info(
            "Campaign created",
            campaign_id=str(campaign.id),
            user_id=str(user.id),
        )

        return RedirectResponse(
            url=f"/campaigns/{campaign.id}?success=Campaign+created+successfully",
            status_code=status.HTTP_302_FOUND,
        )

    except CampaignServiceError as e:
        return RedirectResponse(
            url=f"/campaigns/new?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )
    except Exception as e:
        logger.error("Failed to create campaign", error=str(e))
        return RedirectResponse(
            url="/campaigns/new?error=Failed+to+create+campaign",
            status_code=status.HTTP_302_FOUND,
        )


@router.get("/{campaign_id}", response_class=HTMLResponse)
async def view_campaign(
    request: Request,
    campaign_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """View a campaign."""
    from app.main import templates

    campaign_service = CampaignService(db)
    campaign = await campaign_service.get_campaign(campaign_id, user.id)

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    stats = await campaign_service.get_campaign_stats(campaign_id)

    # Get recent tweets
    recent_tweets = await campaign_service.get_campaign_tweets(
        campaign_id,
        limit=10,
    )

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "campaigns/view.html",
        {
            "request": request,
            "user": user,
            "campaign": campaign,
            "stats": stats,
            "recent_tweets": recent_tweets,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/{campaign_id}/tweets", response_class=HTMLResponse)
async def campaign_tweets(
    request: Request,
    campaign_id: UUID,
    user: CurrentUser,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """View all tweets in a campaign."""
    from app.main import templates

    campaign_service = CampaignService(db)
    campaign = await campaign_service.get_campaign(campaign_id, user.id)

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    # Filter by status if provided
    tweet_status = None
    if status_filter:
        try:
            tweet_status = TweetStatus(status_filter)
        except ValueError:
            pass

    tweets = await campaign_service.get_campaign_tweets(
        campaign_id,
        status=tweet_status,
        limit=100,
    )

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "campaigns/tweets.html",
        {
            "request": request,
            "user": user,
            "campaign": campaign,
            "tweets": tweets,
            "status_filter": status_filter,
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    request: Request,
    campaign_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Pause a campaign."""
    campaign_service = CampaignService(db)
    campaign = await campaign_service.get_campaign(campaign_id, user.id)

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    try:
        await campaign_service.pause_campaign(campaign)
        await db.commit()

        logger.info("Campaign paused", campaign_id=str(campaign_id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?success=Campaign+paused",
            status_code=status.HTTP_302_FOUND,
        )

    except CampaignServiceError as e:
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    request: Request,
    campaign_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Resume a paused campaign."""
    campaign_service = CampaignService(db)
    campaign = await campaign_service.get_campaign(campaign_id, user.id)

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    try:
        await campaign_service.resume_campaign(campaign)
        await db.commit()

        logger.info("Campaign resumed", campaign_id=str(campaign_id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?success=Campaign+resumed",
            status_code=status.HTTP_302_FOUND,
        )

    except CampaignServiceError as e:
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{campaign_id}/cancel")
async def cancel_campaign(
    request: Request,
    campaign_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a campaign."""
    campaign_service = CampaignService(db)
    campaign = await campaign_service.get_campaign(campaign_id, user.id)

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    try:
        await campaign_service.cancel_campaign(campaign)
        await db.commit()

        logger.info("Campaign cancelled", campaign_id=str(campaign_id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?success=Campaign+cancelled",
            status_code=status.HTTP_302_FOUND,
        )

    except CampaignServiceError as e:
        return RedirectResponse(
            url=f"/campaigns/{campaign_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{campaign_id}/delete")
async def delete_campaign(
    request: Request,
    campaign_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Delete a campaign."""
    campaign_service = CampaignService(db)
    campaign = await campaign_service.get_campaign(campaign_id, user.id)

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    await campaign_service.delete_campaign(campaign)
    await db.commit()

    logger.info("Campaign deleted", campaign_id=str(campaign_id), user_id=str(user.id))

    return RedirectResponse(
        url="/campaigns?success=Campaign+deleted",
        status_code=status.HTTP_302_FOUND,
    )
