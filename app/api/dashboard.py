"""Dashboard routes."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.core.database import get_db
from app.models.growth_strategy import (
    EngagementLog,
    GrowthStrategy,
    StrategyStatus,
)
from app.models.tweet import TweetStatus
from app.services.growth_strategy import GrowthStrategyService
from app.services.tweet import TweetService
from app.services.twitter import TwitterService

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Render main dashboard."""
    from app.main import templates

    # Get tweet statistics
    tweet_service = TweetService(db)
    stats = await tweet_service.get_tweet_stats(user.id)

    # Get upcoming scheduled tweets
    upcoming_tweets = await tweet_service.get_user_scheduled_tweets(
        user_id=user.id,
        status=TweetStatus.PENDING,
        limit=5,
    )

    # Get recent posted tweets
    posted_tweets = await tweet_service.get_user_scheduled_tweets(
        user_id=user.id,
        status=TweetStatus.POSTED,
        limit=5,
    )

    # Get failed tweets
    failed_tweets = await tweet_service.get_user_scheduled_tweets(
        user_id=user.id,
        status=TweetStatus.FAILED,
        limit=5,
    )

    # Get Twitter account status
    twitter_service = TwitterService(db)
    twitter_account = await twitter_service.get_oauth_account(user.id)

    # Get growth strategy data
    growth_service = GrowthStrategyService(db)
    try:
        strategies = await growth_service.get_user_strategies(user.id)
    except Exception as e:
        strategies = []
        print(f"Error loading strategies: {e}")

    # Calculate aggregate growth metrics
    growth_stats = {
        "total_strategies": len(strategies),
        "active_strategies": sum(1 for s in strategies if s.status == StrategyStatus.ACTIVE),
        "total_followers_gained": sum(s.followers_gained for s in strategies),
        "total_engagements": sum(
            s.total_follows + s.total_likes + s.total_retweets + s.total_replies + s.total_posts
            for s in strategies
        ),
        "total_follows": sum(s.total_follows for s in strategies),
        "total_likes": sum(s.total_likes for s in strategies),
        "total_replies": sum(s.total_replies for s in strategies),
        "total_posts": sum(s.total_posts for s in strategies),
    }

    # Get active strategy details
    active_strategy = next((s for s in strategies if s.status == StrategyStatus.ACTIVE), None)

    # Get recent engagements (last 24 hours)
    yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_engagements_stmt = (
        select(EngagementLog)
        .join(GrowthStrategy)
        .where(
            GrowthStrategy.user_id == user.id,
            EngagementLog.created_at >= yesterday,
        )
        .order_by(EngagementLog.created_at.desc())
        .limit(10)
    )
    result = await db.execute(recent_engagements_stmt)
    recent_engagements = list(result.scalars().all())

    # Count engagements by type in last 24h
    engagement_counts_stmt = (
        select(
            EngagementLog.engagement_type,
            func.count(EngagementLog.id).label("count"),
        )
        .join(GrowthStrategy)
        .where(
            GrowthStrategy.user_id == user.id,
            EngagementLog.created_at >= yesterday,
        )
        .group_by(EngagementLog.engagement_type)
    )
    result = await db.execute(engagement_counts_stmt)
    engagement_counts = {row.engagement_type.value: row.count for row in result.all()}

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "upcoming_tweets": upcoming_tweets,
            "posted_tweets": posted_tweets,
            "failed_tweets": failed_tweets,
            "twitter_account": twitter_account,
            "growth_stats": growth_stats,
            "active_strategy": active_strategy,
            "strategies": strategies,
            "recent_engagements": recent_engagements,
            "engagement_counts": engagement_counts,
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def tweet_history(
    request: Request,
    user: CurrentUser,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    """Render tweet history page."""
    from app.main import templates

    limit = 20
    offset = (page - 1) * limit

    tweet_service = TweetService(db)

    # Get all tweets sorted by creation date
    tweets = await tweet_service.get_user_scheduled_tweets(
        user_id=user.id,
        limit=limit,
        offset=offset,
    )

    return templates.TemplateResponse(
        "dashboard/history.html",
        {
            "request": request,
            "user": user,
            "tweets": tweets,
            "page": page,
            "has_more": len(tweets) == limit,
        },
    )


@router.get("/drafts", response_class=HTMLResponse)
async def drafts(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Render drafts page."""
    from app.main import templates

    tweet_service = TweetService(db)
    drafts = await tweet_service.get_user_drafts(user_id=user.id, limit=50)

    return templates.TemplateResponse(
        "dashboard/drafts.html",
        {
            "request": request,
            "user": user,
            "drafts": drafts,
        },
    )
