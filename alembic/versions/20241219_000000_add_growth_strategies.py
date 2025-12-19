"""Add growth strategies feature

Revision ID: 003
Revises: 002
Create Date: 2024-12-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create strategystatus enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'strategystatus') THEN
                CREATE TYPE strategystatus AS ENUM (
                    'DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED'
                );
            END IF;
        END
        $$;
    """)

    # Create verificationstatus enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'verificationstatus') THEN
                CREATE TYPE verificationstatus AS ENUM (
                    'NONE', 'BLUE', 'YELLOW'
                );
            END IF;
        END
        $$;
    """)

    # Create targettype enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'targettype') THEN
                CREATE TYPE targettype AS ENUM (
                    'ACCOUNT', 'TWEET'
                );
            END IF;
        END
        $$;
    """)

    # Create engagementstatus enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'engagementstatus') THEN
                CREATE TYPE engagementstatus AS ENUM (
                    'PENDING', 'COMPLETED', 'FAILED', 'SKIPPED'
                );
            END IF;
        END
        $$;
    """)

    # Create actiontype enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'actiontype') THEN
                CREATE TYPE actiontype AS ENUM (
                    'FOLLOW', 'UNFOLLOW', 'LIKE', 'UNLIKE', 'RETWEET', 'UNRETWEET', 'REPLY', 'QUOTE_TWEET'
                );
            END IF;
        END
        $$;
    """)

    # Add growth strategy audit actions to auditaction enum
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'GROWTH_STRATEGY_CREATED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'GROWTH_STRATEGY_ACTIVATED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'GROWTH_STRATEGY_PAUSED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'GROWTH_STRATEGY_RESUMED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'GROWTH_STRATEGY_CANCELLED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'GROWTH_STRATEGY_COMPLETED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'ENGAGEMENT_FOLLOW'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'ENGAGEMENT_LIKE'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'ENGAGEMENT_RETWEET'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'ENGAGEMENT_REPLY'")

    # Create growth_strategies table
    op.create_table(
        "growth_strategies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("original_prompt", sa.Text(), nullable=False),
        # Account info
        sa.Column(
            "verification_status",
            postgresql.ENUM(
                "NONE", "BLUE", "YELLOW",
                name="verificationstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="NONE",
        ),
        sa.Column("tweet_char_limit", sa.Integer(), nullable=False, server_default="280"),
        sa.Column("starting_followers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_followers", sa.Integer(), nullable=False, server_default="0"),
        # Strategy configuration
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED",
                name="strategystatus",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        # Goals
        sa.Column("target_followers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_engagement_rate", sa.Float(), nullable=False, server_default="5.0"),
        # Daily quotas
        sa.Column("daily_follows", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("daily_unfollows", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("daily_likes", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("daily_retweets", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("daily_replies", sa.Integer(), nullable=False, server_default="20"),
        # Strategy parameters
        sa.Column("niche_keywords", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("target_accounts", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("avoid_accounts", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("engagement_hours_start", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("engagement_hours_end", sa.Integer(), nullable=False, server_default="21"),
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="'UTC'"),
        # AI-generated plan
        sa.Column("strategy_plan", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("estimated_results", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        # Progress tracking
        sa.Column("total_follows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_unfollows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_retweets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_replies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("followers_gained", sa.Integer(), nullable=False, server_default="0"),
        # Settings
        sa.Column("auto_reply_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("require_reply_approval", sa.Boolean(), nullable=False, server_default="false"),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_growth_strategies_id", "growth_strategies", ["id"], unique=False)
    op.create_index("ix_growth_strategies_user_id", "growth_strategies", ["user_id"], unique=False)
    op.create_index("ix_growth_strategies_status", "growth_strategies", ["status"], unique=False)
    op.create_index("ix_growth_strategies_deleted_at", "growth_strategies", ["deleted_at"], unique=False)

    # Create engagement_targets table
    op.create_table(
        "engagement_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "target_type",
            postgresql.ENUM(
                "ACCOUNT", "TWEET",
                name="targettype",
                create_type=False,
            ),
            nullable=False,
        ),
        # For accounts
        sa.Column("twitter_user_id", sa.String(length=50), nullable=True),
        sa.Column("twitter_username", sa.String(length=50), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("following_count", sa.Integer(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        # For tweets
        sa.Column("tweet_id", sa.String(length=50), nullable=True),
        sa.Column("tweet_author", sa.String(length=50), nullable=True),
        sa.Column("tweet_author_id", sa.String(length=50), nullable=True),
        sa.Column("tweet_content", sa.Text(), nullable=True),
        sa.Column("tweet_like_count", sa.Integer(), nullable=True),
        sa.Column("tweet_retweet_count", sa.Integer(), nullable=True),
        # Actions
        sa.Column("should_follow", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("should_like", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("should_retweet", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("should_reply", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reply_content", sa.Text(), nullable=True),
        sa.Column("reply_approved", sa.Boolean(), nullable=False, server_default="false"),
        # Status
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "COMPLETED", "FAILED", "SKIPPED",
                name="engagementstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # Scoring
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["growth_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engagement_targets_id", "engagement_targets", ["id"], unique=False)
    op.create_index("ix_engagement_targets_strategy_id", "engagement_targets", ["strategy_id"], unique=False)
    op.create_index("ix_engagement_targets_status", "engagement_targets", ["status"], unique=False)
    op.create_index("ix_engagement_targets_scheduled_for", "engagement_targets", ["scheduled_for"], unique=False)
    op.create_index("ix_engagement_targets_priority", "engagement_targets", ["priority"], unique=False)
    op.create_index("ix_engagement_targets_twitter_user_id", "engagement_targets", ["twitter_user_id"], unique=False)
    op.create_index("ix_engagement_targets_tweet_id", "engagement_targets", ["tweet_id"], unique=False)

    # Create engagement_logs table
    op.create_table(
        "engagement_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "action_type",
            postgresql.ENUM(
                "FOLLOW", "UNFOLLOW", "LIKE", "UNLIKE", "RETWEET", "UNRETWEET", "REPLY", "QUOTE_TWEET",
                name="actiontype",
                create_type=False,
            ),
            nullable=False,
        ),
        # Target info
        sa.Column("twitter_user_id", sa.String(length=50), nullable=True),
        sa.Column("twitter_username", sa.String(length=50), nullable=True),
        sa.Column("tweet_id", sa.String(length=50), nullable=True),
        # Result
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        # For replies
        sa.Column("reply_content", sa.Text(), nullable=True),
        sa.Column("reply_tweet_id", sa.String(length=50), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["growth_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engagement_logs_id", "engagement_logs", ["id"], unique=False)
    op.create_index("ix_engagement_logs_strategy_id", "engagement_logs", ["strategy_id"], unique=False)
    op.create_index("ix_engagement_logs_action_type", "engagement_logs", ["action_type"], unique=False)

    # Create daily_progress table
    op.create_table(
        "daily_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        # Daily counts
        sa.Column("follows_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unfollows_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retweets_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replies_done", sa.Integer(), nullable=False, server_default="0"),
        # Metrics snapshot
        sa.Column("follower_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("following_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagement_rate", sa.Float(), nullable=False, server_default="0.0"),
        # AI observations
        sa.Column("ai_observations", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["strategy_id"], ["growth_strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_daily_progress_id", "daily_progress", ["id"], unique=False)
    op.create_index("ix_daily_progress_strategy_id", "daily_progress", ["strategy_id"], unique=False)
    op.create_index("ix_daily_progress_date", "daily_progress", ["date"], unique=False)

    # Create rate_limit_trackers table
    op.create_table(
        "rate_limit_trackers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        # Daily counts
        sa.Column("follows_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unfollows_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("posts_count", sa.Integer(), nullable=False, server_default="0"),
        # Last reset
        sa.Column("last_reset", sa.DateTime(timezone=True), nullable=False),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rate_limit_trackers_id", "rate_limit_trackers", ["id"], unique=False)
    op.create_index("ix_rate_limit_trackers_user_id", "rate_limit_trackers", ["user_id"], unique=False)
    op.create_index("ix_rate_limit_trackers_date", "rate_limit_trackers", ["date"], unique=False)


def downgrade() -> None:
    # Drop rate_limit_trackers table
    op.drop_index("ix_rate_limit_trackers_date", table_name="rate_limit_trackers")
    op.drop_index("ix_rate_limit_trackers_user_id", table_name="rate_limit_trackers")
    op.drop_index("ix_rate_limit_trackers_id", table_name="rate_limit_trackers")
    op.drop_table("rate_limit_trackers")

    # Drop daily_progress table
    op.drop_index("ix_daily_progress_date", table_name="daily_progress")
    op.drop_index("ix_daily_progress_strategy_id", table_name="daily_progress")
    op.drop_index("ix_daily_progress_id", table_name="daily_progress")
    op.drop_table("daily_progress")

    # Drop engagement_logs table
    op.drop_index("ix_engagement_logs_action_type", table_name="engagement_logs")
    op.drop_index("ix_engagement_logs_strategy_id", table_name="engagement_logs")
    op.drop_index("ix_engagement_logs_id", table_name="engagement_logs")
    op.drop_table("engagement_logs")

    # Drop engagement_targets table
    op.drop_index("ix_engagement_targets_tweet_id", table_name="engagement_targets")
    op.drop_index("ix_engagement_targets_twitter_user_id", table_name="engagement_targets")
    op.drop_index("ix_engagement_targets_priority", table_name="engagement_targets")
    op.drop_index("ix_engagement_targets_scheduled_for", table_name="engagement_targets")
    op.drop_index("ix_engagement_targets_status", table_name="engagement_targets")
    op.drop_index("ix_engagement_targets_strategy_id", table_name="engagement_targets")
    op.drop_index("ix_engagement_targets_id", table_name="engagement_targets")
    op.drop_table("engagement_targets")

    # Drop growth_strategies table
    op.drop_index("ix_growth_strategies_deleted_at", table_name="growth_strategies")
    op.drop_index("ix_growth_strategies_status", table_name="growth_strategies")
    op.drop_index("ix_growth_strategies_user_id", table_name="growth_strategies")
    op.drop_index("ix_growth_strategies_id", table_name="growth_strategies")
    op.drop_table("growth_strategies")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS actiontype")
    op.execute("DROP TYPE IF EXISTS engagementstatus")
    op.execute("DROP TYPE IF EXISTS targettype")
    op.execute("DROP TYPE IF EXISTS verificationstatus")
    op.execute("DROP TYPE IF EXISTS strategystatus")

    # Note: Removing audit action enum values is not easily supported in PostgreSQL
