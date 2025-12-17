"""Dashboard routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.core.database import get_db
from app.models.tweet import TweetStatus
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
