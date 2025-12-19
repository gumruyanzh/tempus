"""Admin routes."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import generate_csrf_token
from app.models.audit import AuditLog
from app.models.campaign import AutoCampaign, CampaignStatus
from app.models.growth_strategy import (
    DailyProgress,
    EngagementLog,
    EngagementTarget,
    GrowthStrategy,
    StrategyStatus,
)
from app.models.system_log import LogCategory, LogLevel, SystemLog, TaskExecution
from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import User, UserRole
from app.services.growth_strategy import GrowthStrategyService
from app.services.system_logging import SystemLoggingService, TaskExecutionService
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


# =============================================================================
# SUPERADMIN MONITORING ROUTES
# =============================================================================


@router.get("/monitoring", response_class=HTMLResponse)
async def admin_monitoring_dashboard(
    request: Request,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Superadmin monitoring dashboard with system health and stats."""
    from app.main import templates

    # Get log statistics
    log_service = SystemLoggingService(db)
    log_stats = await log_service.get_log_stats(hours=24)

    # Get task execution statistics
    task_service = TaskExecutionService(db)
    task_stats = await task_service.get_task_stats(hours=24)

    # Get growth strategy statistics
    strategies_stmt = select(func.count()).select_from(GrowthStrategy).where(
        GrowthStrategy.deleted_at.is_(None)
    )
    strategies_result = await db.execute(strategies_stmt)
    total_strategies = strategies_result.scalar() or 0

    active_strategies_stmt = select(func.count()).select_from(GrowthStrategy).where(
        GrowthStrategy.status == StrategyStatus.ACTIVE,
        GrowthStrategy.deleted_at.is_(None),
    )
    active_result = await db.execute(active_strategies_stmt)
    active_strategies = active_result.scalar() or 0

    # Get campaign statistics
    campaigns_stmt = select(func.count()).select_from(AutoCampaign).where(
        AutoCampaign.deleted_at.is_(None)
    )
    campaigns_result = await db.execute(campaigns_stmt)
    total_campaigns = campaigns_result.scalar() or 0

    active_campaigns_stmt = select(func.count()).select_from(AutoCampaign).where(
        AutoCampaign.status == CampaignStatus.ACTIVE,
        AutoCampaign.deleted_at.is_(None),
    )
    active_campaigns_result = await db.execute(active_campaigns_stmt)
    active_campaigns = active_campaigns_result.scalar() or 0

    # Get recent system logs (errors and warnings)
    recent_issues = await log_service.get_logs(
        level=LogLevel.ERROR,
        limit=10,
    )

    # Get recent task executions
    recent_tasks = await task_service.get_recent_executions(limit=20)

    return templates.TemplateResponse(
        "admin/monitoring.html",
        {
            "request": request,
            "user": admin,
            "log_stats": log_stats,
            "task_stats": task_stats,
            "total_strategies": total_strategies,
            "active_strategies": active_strategies,
            "total_campaigns": total_campaigns,
            "active_campaigns": active_campaigns,
            "recent_issues": recent_issues,
            "recent_tasks": recent_tasks,
        },
    )


@router.get("/monitoring/logs", response_class=HTMLResponse)
async def admin_system_logs(
    request: Request,
    admin: AdminUser,
    page: int = 1,
    level: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    """View system logs with filtering."""
    from app.main import templates

    limit = 100
    offset = (page - 1) * limit
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    log_service = SystemLoggingService(db)

    # Parse filters
    level_enum = None
    if level:
        try:
            level_enum = LogLevel(level)
        except ValueError:
            pass

    category_enum = None
    if category:
        try:
            category_enum = LogCategory(category)
        except ValueError:
            pass

    logs = await log_service.get_logs(
        level=level_enum,
        category=category_enum,
        search=search,
        since=since,
        limit=limit,
        offset=offset,
    )

    return templates.TemplateResponse(
        "admin/system_logs.html",
        {
            "request": request,
            "user": admin,
            "logs": logs,
            "page": page,
            "level": level,
            "category": category,
            "search": search,
            "hours": hours,
            "levels": [l.value for l in LogLevel],
            "categories": [c.value for c in LogCategory],
            "has_more": len(logs) == limit,
        },
    )


@router.get("/monitoring/tasks", response_class=HTMLResponse)
async def admin_task_executions(
    request: Request,
    admin: AdminUser,
    page: int = 1,
    task_name: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """View Celery task executions."""
    from app.main import templates

    limit = 50
    offset = (page - 1) * limit

    task_service = TaskExecutionService(db)
    executions = await task_service.get_recent_executions(
        task_name=task_name,
        status=status,
        limit=limit,
    )

    # Get unique task names for filter
    task_names_stmt = (
        select(TaskExecution.task_name)
        .distinct()
        .order_by(TaskExecution.task_name)
    )
    task_names_result = await db.execute(task_names_stmt)
    task_names = [row[0] for row in task_names_result.fetchall()]

    # Get stats
    task_stats = await task_service.get_task_stats(hours=24)

    return templates.TemplateResponse(
        "admin/task_executions.html",
        {
            "request": request,
            "user": admin,
            "executions": executions,
            "page": page,
            "task_name": task_name,
            "status": status,
            "task_names": task_names,
            "task_stats": task_stats,
            "has_more": len(executions) == limit,
        },
    )


@router.get("/monitoring/growth", response_class=HTMLResponse)
async def admin_growth_monitoring(
    request: Request,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Monitor all growth strategies."""
    from app.main import templates

    # Get all strategies with their progress
    strategies_stmt = (
        select(GrowthStrategy)
        .where(GrowthStrategy.deleted_at.is_(None))
        .order_by(GrowthStrategy.created_at.desc())
    )
    strategies_result = await db.execute(strategies_stmt)
    strategies = strategies_result.scalars().all()

    # Get recent engagement logs
    engagement_logs_stmt = (
        select(EngagementLog)
        .order_by(EngagementLog.created_at.desc())
        .limit(50)
    )
    engagement_result = await db.execute(engagement_logs_stmt)
    recent_engagements = engagement_result.scalars().all()

    # Get pending targets count
    pending_targets_stmt = select(func.count()).select_from(EngagementTarget).where(
        EngagementTarget.status == "pending"
    )
    pending_result = await db.execute(pending_targets_stmt)
    pending_targets = pending_result.scalar() or 0

    # Get today's progress across all strategies
    today = datetime.now(timezone.utc).date()
    daily_progress_stmt = (
        select(DailyProgress)
        .where(DailyProgress.date == today)
    )
    daily_result = await db.execute(daily_progress_stmt)
    today_progress = daily_result.scalars().all()

    # Aggregate today's stats
    today_stats = {
        "follows": sum(p.follows_done for p in today_progress),
        "likes": sum(p.likes_done for p in today_progress),
        "retweets": sum(p.retweets_done for p in today_progress),
        "replies": sum(p.replies_done for p in today_progress),
    }

    return templates.TemplateResponse(
        "admin/growth_monitoring.html",
        {
            "request": request,
            "user": admin,
            "strategies": strategies,
            "recent_engagements": recent_engagements,
            "pending_targets": pending_targets,
            "today_stats": today_stats,
        },
    )


@router.get("/monitoring/growth/{strategy_id}", response_class=HTMLResponse)
async def admin_growth_strategy_detail(
    request: Request,
    strategy_id: UUID,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """View detailed monitoring for a specific growth strategy."""
    from app.main import templates

    # Get strategy
    stmt = select(GrowthStrategy).where(GrowthStrategy.id == strategy_id)
    result = await db.execute(stmt)
    strategy = result.scalar_one_or_none()

    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Get strategy analytics
    growth_service = GrowthStrategyService(db)
    analytics = await growth_service.get_strategy_analytics(strategy_id)

    # Get recent engagement logs for this strategy
    engagement_logs_stmt = (
        select(EngagementLog)
        .where(EngagementLog.strategy_id == strategy_id)
        .order_by(EngagementLog.created_at.desc())
        .limit(100)
    )
    engagement_result = await db.execute(engagement_logs_stmt)
    engagement_logs = engagement_result.scalars().all()

    # Get pending targets
    targets_stmt = (
        select(EngagementTarget)
        .where(
            EngagementTarget.strategy_id == strategy_id,
            EngagementTarget.status == "pending",
        )
        .limit(50)
    )
    targets_result = await db.execute(targets_stmt)
    pending_targets = targets_result.scalars().all()

    # Get system logs for this strategy
    log_service = SystemLoggingService(db)
    strategy_logs = await log_service.get_logs(
        strategy_id=strategy_id,
        limit=50,
    )

    return templates.TemplateResponse(
        "admin/growth_strategy_detail.html",
        {
            "request": request,
            "user": admin,
            "strategy": strategy,
            "analytics": analytics,
            "engagement_logs": engagement_logs,
            "pending_targets": pending_targets,
            "strategy_logs": strategy_logs,
        },
    )


# =============================================================================
# API ENDPOINTS FOR REAL-TIME UPDATES
# =============================================================================


@router.get("/api/logs/stream")
async def stream_logs(
    admin: AdminUser,
    since_id: Optional[str] = None,
    level: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """API endpoint for fetching latest logs (for polling)."""
    log_service = SystemLoggingService(db)

    level_enum = None
    if level:
        try:
            level_enum = LogLevel(level)
        except ValueError:
            pass

    category_enum = None
    if category:
        try:
            category_enum = LogCategory(category)
        except ValueError:
            pass

    logs = await log_service.get_logs(
        level=level_enum,
        category=category_enum,
        limit=limit,
    )

    return JSONResponse({
        "logs": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat(),
                "level": log.level.value,
                "category": log.category.value,
                "logger_name": log.logger_name,
                "message": log.message,
                "task_name": log.task_name,
                "details": log.details,
                "exception_type": log.exception_type,
                "exception_message": log.exception_message,
            }
            for log in logs
        ],
        "count": len(logs),
    })


@router.get("/api/tasks/stats")
async def get_task_stats(
    admin: AdminUser,
    hours: int = Query(default=24, le=168),
    db: AsyncSession = Depends(get_db),
):
    """API endpoint for task execution statistics."""
    task_service = TaskExecutionService(db)
    stats = await task_service.get_task_stats(hours=hours)

    # Convert TaskExecution objects to dicts
    stats["recent_failures"] = [
        {
            "id": str(t.id),
            "task_id": t.task_id,
            "task_name": t.task_name,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "error_message": t.error_message,
        }
        for t in stats["recent_failures"]
    ]

    return JSONResponse(stats)


@router.get("/api/growth/stats")
async def get_growth_stats(
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """API endpoint for growth strategy statistics."""
    # Active strategies count
    active_stmt = select(func.count()).select_from(GrowthStrategy).where(
        GrowthStrategy.status == StrategyStatus.ACTIVE,
        GrowthStrategy.deleted_at.is_(None),
    )
    active_result = await db.execute(active_stmt)
    active_count = active_result.scalar() or 0

    # Today's engagements
    today = datetime.now(timezone.utc).date()
    engagements_stmt = select(func.count()).select_from(EngagementLog).where(
        func.date(EngagementLog.created_at) == today
    )
    engagements_result = await db.execute(engagements_stmt)
    today_engagements = engagements_result.scalar() or 0

    # Pending targets
    pending_stmt = select(func.count()).select_from(EngagementTarget).where(
        EngagementTarget.status == "pending"
    )
    pending_result = await db.execute(pending_stmt)
    pending_targets = pending_result.scalar() or 0

    return JSONResponse({
        "active_strategies": active_count,
        "today_engagements": today_engagements,
        "pending_targets": pending_targets,
    })
