"""Tweet management routes."""

from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

import pytz
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_client_ip
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import generate_csrf_token
from app.services.audit import AuditService
from app.services.tweet import TweetService, TweetServiceError

logger = get_logger(__name__)

router = APIRouter()


@router.get("/new", response_class=HTMLResponse)
async def new_tweet_page(
    request: Request,
    user: CurrentUser,
    draft_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Render new tweet page."""
    from app.main import templates

    draft = None
    if draft_id:
        tweet_service = TweetService(db)
        draft = await tweet_service.get_draft(UUID(draft_id), user.id)

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "tweets/new.html",
        {
            "request": request,
            "user": user,
            "draft": draft,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/schedule")
async def schedule_tweet(
    request: Request,
    user: CurrentUser,
    content: Annotated[str, Form()],
    scheduled_date: Annotated[str, Form()],
    scheduled_time: Annotated[str, Form()],
    user_timezone: Annotated[str, Form()] = "UTC",
    is_thread: Annotated[bool, Form()] = False,
    thread_content: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Schedule a new tweet."""
    tweet_service = TweetService(db)
    audit_service = AuditService(db)

    try:
        # Parse scheduled datetime
        scheduled_datetime_str = f"{scheduled_date} {scheduled_time}"
        local_tz = pytz.timezone(user_timezone)
        local_dt = datetime.strptime(scheduled_datetime_str, "%Y-%m-%d %H:%M")
        local_dt = local_tz.localize(local_dt)
        utc_dt = local_dt.astimezone(pytz.UTC)

        # Parse thread content if applicable
        thread_contents = None
        if is_thread and thread_content:
            thread_contents = [
                t.strip() for t in thread_content.split("\n---\n") if t.strip()
            ]

        # Schedule the tweet
        scheduled_tweet = await tweet_service.schedule_tweet(
            user_id=user.id,
            content=content,
            scheduled_for=utc_dt,
            timezone_str=user_timezone,
            is_thread=is_thread,
            thread_contents=thread_contents,
        )

        # Log audit
        await audit_service.log_tweet_scheduled(
            user_id=user.id,
            tweet_id=scheduled_tweet.id,
            scheduled_for=utc_dt.isoformat(),
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info(
            "Tweet scheduled",
            tweet_id=str(scheduled_tweet.id),
            user_id=str(user.id),
        )

        return RedirectResponse(
            url="/dashboard?success=Tweet+scheduled+successfully",
            status_code=status.HTTP_302_FOUND,
        )

    except TweetServiceError as e:
        return RedirectResponse(
            url=f"/tweets/new?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )
    except Exception as e:
        logger.error("Failed to schedule tweet", error=str(e))
        return RedirectResponse(
            url="/tweets/new?error=Failed+to+schedule+tweet",
            status_code=status.HTTP_302_FOUND,
        )


@router.get("/{tweet_id}", response_class=HTMLResponse)
async def view_tweet(
    request: Request,
    tweet_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """View a scheduled tweet."""
    from app.main import templates

    tweet_service = TweetService(db)
    tweet = await tweet_service.get_scheduled_tweet(tweet_id, user.id)

    if not tweet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tweet not found",
        )

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "tweets/view.html",
        {
            "request": request,
            "user": user,
            "tweet": tweet,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/{tweet_id}/edit", response_class=HTMLResponse)
async def edit_tweet_page(
    request: Request,
    tweet_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Render edit tweet page."""
    from app.main import templates

    tweet_service = TweetService(db)
    tweet = await tweet_service.get_scheduled_tweet(tweet_id, user.id)

    if not tweet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tweet not found",
        )

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "tweets/edit.html",
        {
            "request": request,
            "user": user,
            "tweet": tweet,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/{tweet_id}/edit")
async def edit_tweet(
    request: Request,
    tweet_id: UUID,
    user: CurrentUser,
    content: Annotated[str, Form()],
    scheduled_date: Annotated[str, Form()],
    scheduled_time: Annotated[str, Form()],
    user_timezone: Annotated[str, Form()] = "UTC",
    db: AsyncSession = Depends(get_db),
):
    """Update a scheduled tweet."""
    tweet_service = TweetService(db)

    tweet = await tweet_service.get_scheduled_tweet(tweet_id, user.id)
    if not tweet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tweet not found",
        )

    try:
        # Parse scheduled datetime
        scheduled_datetime_str = f"{scheduled_date} {scheduled_time}"
        local_tz = pytz.timezone(user_timezone)
        local_dt = datetime.strptime(scheduled_datetime_str, "%Y-%m-%d %H:%M")
        local_dt = local_tz.localize(local_dt)
        utc_dt = local_dt.astimezone(pytz.UTC)

        await tweet_service.update_scheduled_tweet(
            tweet=tweet,
            content=content,
            scheduled_for=utc_dt,
        )
        await db.commit()

        logger.info("Tweet updated", tweet_id=str(tweet_id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/tweets/{tweet_id}?success=Tweet+updated+successfully",
            status_code=status.HTTP_302_FOUND,
        )

    except TweetServiceError as e:
        return RedirectResponse(
            url=f"/tweets/{tweet_id}/edit?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{tweet_id}/cancel")
async def cancel_tweet(
    request: Request,
    tweet_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a scheduled tweet."""
    tweet_service = TweetService(db)
    audit_service = AuditService(db)

    tweet = await tweet_service.get_scheduled_tweet(tweet_id, user.id)
    if not tweet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tweet not found",
        )

    try:
        await tweet_service.cancel_scheduled_tweet(tweet)

        await audit_service.log_tweet_cancelled(
            user_id=user.id,
            tweet_id=tweet_id,
            ip_address=get_client_ip(request),
        )
        await db.commit()

        logger.info("Tweet cancelled", tweet_id=str(tweet_id), user_id=str(user.id))

        return RedirectResponse(
            url="/dashboard?success=Tweet+cancelled",
            status_code=status.HTTP_302_FOUND,
        )

    except TweetServiceError as e:
        return RedirectResponse(
            url=f"/tweets/{tweet_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{tweet_id}/delete")
async def delete_tweet(
    request: Request,
    tweet_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Delete a scheduled tweet."""
    tweet_service = TweetService(db)

    tweet = await tweet_service.get_scheduled_tweet(tweet_id, user.id)
    if not tweet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tweet not found",
        )

    await tweet_service.delete_scheduled_tweet(tweet)
    await db.commit()

    logger.info("Tweet deleted", tweet_id=str(tweet_id), user_id=str(user.id))

    return RedirectResponse(
        url="/dashboard?success=Tweet+deleted",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{tweet_id}/duplicate")
async def duplicate_tweet(
    request: Request,
    tweet_id: UUID,
    user: CurrentUser,
    scheduled_date: Annotated[str, Form()],
    scheduled_time: Annotated[str, Form()],
    user_timezone: Annotated[str, Form()] = "UTC",
    db: AsyncSession = Depends(get_db),
):
    """Duplicate a tweet with a new schedule."""
    tweet_service = TweetService(db)

    tweet = await tweet_service.get_scheduled_tweet(tweet_id, user.id)
    if not tweet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tweet not found",
        )

    try:
        # Parse scheduled datetime
        scheduled_datetime_str = f"{scheduled_date} {scheduled_time}"
        local_tz = pytz.timezone(user_timezone)
        local_dt = datetime.strptime(scheduled_datetime_str, "%Y-%m-%d %H:%M")
        local_dt = local_tz.localize(local_dt)
        utc_dt = local_dt.astimezone(pytz.UTC)

        new_tweet = await tweet_service.duplicate_scheduled_tweet(
            tweet=tweet,
            new_scheduled_for=utc_dt,
        )
        await db.commit()

        logger.info(
            "Tweet duplicated",
            original_id=str(tweet_id),
            new_id=str(new_tweet.id),
            user_id=str(user.id),
        )

        return RedirectResponse(
            url=f"/tweets/{new_tweet.id}?success=Tweet+duplicated",
            status_code=status.HTTP_302_FOUND,
        )

    except TweetServiceError as e:
        return RedirectResponse(
            url=f"/tweets/{tweet_id}?error={str(e).replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post("/{tweet_id}/retry")
async def retry_tweet(
    request: Request,
    tweet_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed tweet."""
    from app.tasks.tweet_tasks import retry_failed_tweet

    tweet_service = TweetService(db)

    tweet = await tweet_service.get_scheduled_tweet(tweet_id, user.id)
    if not tweet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tweet not found",
        )

    if not tweet.can_retry:
        return RedirectResponse(
            url=f"/tweets/{tweet_id}?error=Tweet+cannot+be+retried",
            status_code=status.HTTP_302_FOUND,
        )

    # Queue for retry
    retry_failed_tweet.delay(str(tweet_id))

    logger.info("Tweet queued for retry", tweet_id=str(tweet_id), user_id=str(user.id))

    return RedirectResponse(
        url=f"/tweets/{tweet_id}?success=Tweet+queued+for+retry",
        status_code=status.HTTP_302_FOUND,
    )
