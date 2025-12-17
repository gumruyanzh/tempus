"""Admin routes."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import generate_csrf_token
from app.models.audit import AuditLog
from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import User, UserRole
from app.services.user import UserService

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Render admin dashboard."""
    from app.main import templates

    # Get user statistics
    user_count_stmt = select(func.count()).select_from(User).where(User.deleted_at.is_(None))
    user_count_result = await db.execute(user_count_stmt)
    total_users = user_count_result.scalar() or 0

    active_users_stmt = (
        select(func.count())
        .select_from(User)
        .where(User.is_active == True, User.deleted_at.is_(None))
    )
    active_users_result = await db.execute(active_users_stmt)
    active_users = active_users_result.scalar() or 0

    # Get tweet statistics
    total_tweets_stmt = select(func.count()).select_from(ScheduledTweet)
    total_tweets_result = await db.execute(total_tweets_stmt)
    total_tweets = total_tweets_result.scalar() or 0

    posted_tweets_stmt = (
        select(func.count())
        .select_from(ScheduledTweet)
        .where(ScheduledTweet.status == TweetStatus.POSTED)
    )
    posted_tweets_result = await db.execute(posted_tweets_stmt)
    posted_tweets = posted_tweets_result.scalar() or 0

    pending_tweets_stmt = (
        select(func.count())
        .select_from(ScheduledTweet)
        .where(ScheduledTweet.status == TweetStatus.PENDING)
    )
    pending_tweets_result = await db.execute(pending_tweets_stmt)
    pending_tweets = pending_tweets_result.scalar() or 0

    # Get recent audit logs
    audit_logs_stmt = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    )
    audit_logs_result = await db.execute(audit_logs_stmt)
    recent_logs = audit_logs_result.scalars().all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": admin,
            "total_users": total_users,
            "active_users": active_users,
            "total_tweets": total_tweets,
            "posted_tweets": posted_tweets,
            "pending_tweets": pending_tweets,
            "recent_logs": recent_logs,
        },
    )


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    admin: AdminUser,
    page: int = 1,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all users."""
    from app.main import templates

    limit = 20
    offset = (page - 1) * limit

    query = select(User).where(User.deleted_at.is_(None))

    if search:
        query = query.where(User.email.ilike(f"%{search}%"))

    query = query.order_by(User.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    users = result.scalars().all()

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "user": admin,
            "users": users,
            "page": page,
            "search": search,
            "has_more": len(users) == limit,
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(
    request: Request,
    user_id: UUID,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """View user details."""
    from app.main import templates

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get user's audit logs
    audit_logs_stmt = (
        select(AuditLog)
        .where(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    )
    audit_logs_result = await db.execute(audit_logs_stmt)
    audit_logs = audit_logs_result.scalars().all()

    # Get user's tweet stats
    tweets_stmt = (
        select(func.count())
        .select_from(ScheduledTweet)
        .where(ScheduledTweet.user_id == user_id)
    )
    tweets_result = await db.execute(tweets_stmt)
    tweet_count = tweets_result.scalar() or 0

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "admin/user_detail.html",
        {
            "request": request,
            "user": admin,
            "target_user": target_user,
            "audit_logs": audit_logs,
            "tweet_count": tweet_count,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(
    request: Request,
    user_id: UUID,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Toggle user active status."""
    if user_id == admin.id:
        return RedirectResponse(
            url=f"/admin/users/{user_id}?error=Cannot+modify+your+own+account",
            status_code=302,
        )

    user_service = UserService(db)
    target_user = await user_service.get_user(user_id)

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if target_user.is_active:
        await user_service.deactivate_user(target_user)
        action = "deactivated"
    else:
        await user_service.activate_user(target_user)
        action = "activated"

    await db.commit()

    logger.info(
        f"User {action} by admin",
        target_user_id=str(user_id),
        admin_id=str(admin.id),
    )

    return RedirectResponse(
        url=f"/admin/users/{user_id}?success=User+{action}",
        status_code=302,
    )


@router.post("/users/{user_id}/toggle-role")
async def toggle_user_role(
    request: Request,
    user_id: UUID,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Toggle user role between user and admin."""
    if user_id == admin.id:
        return RedirectResponse(
            url=f"/admin/users/{user_id}?error=Cannot+modify+your+own+role",
            status_code=302,
        )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if target_user.role == UserRole.ADMIN:
        target_user.role = UserRole.USER
        new_role = "user"
    else:
        target_user.role = UserRole.ADMIN
        new_role = "admin"

    await db.commit()

    logger.info(
        "User role changed by admin",
        target_user_id=str(user_id),
        new_role=new_role,
        admin_id=str(admin.id),
    )

    return RedirectResponse(
        url=f"/admin/users/{user_id}?success=Role+changed+to+{new_role}",
        status_code=302,
    )


@router.get("/audit-logs", response_class=HTMLResponse)
async def admin_audit_logs(
    request: Request,
    admin: AdminUser,
    page: int = 1,
    action_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """View audit logs."""
    from app.main import templates
    from app.models.audit import AuditAction

    limit = 50
    offset = (page - 1) * limit

    query = select(AuditLog)

    if action_filter:
        try:
            action_enum = AuditAction(action_filter)
            query = query.where(AuditLog.action == action_enum)
        except ValueError:
            pass

    query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    logs = result.scalars().all()

    return templates.TemplateResponse(
        "admin/audit_logs.html",
        {
            "request": request,
            "user": admin,
            "logs": logs,
            "page": page,
            "action_filter": action_filter,
            "actions": [a.value for a in AuditAction],
            "has_more": len(logs) == limit,
        },
    )
