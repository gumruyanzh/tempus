"""Add campaigns feature

Revision ID: 002
Revises: 001
Create Date: 2024-12-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add TAVILY to apikeytype enum
    op.execute("ALTER TYPE apikeytype ADD VALUE IF NOT EXISTS 'TAVILY'")

    # Add AWAITING_GENERATION to tweetstatus enum
    op.execute("ALTER TYPE tweetstatus ADD VALUE IF NOT EXISTS 'AWAITING_GENERATION' AFTER 'PENDING'")

    # Add campaign audit actions to auditaction enum
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'CAMPAIGN_CREATED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'CAMPAIGN_PAUSED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'CAMPAIGN_RESUMED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'CAMPAIGN_CANCELLED'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'CAMPAIGN_COMPLETED'")

    # Create campaignstatus enum
    op.execute("""
        CREATE TYPE campaignstatus AS ENUM (
            'DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED'
        )
    """)

    # Create auto_campaigns table
    op.create_table(
        "auto_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("original_prompt", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column(
            "tone",
            sa.Enum(
                "PROFESSIONAL",
                "CASUAL",
                "VIRAL",
                "THOUGHT_LEADERSHIP",
                name="tweettone",
                create_type=False,
            ),
            nullable=False,
            server_default="PROFESSIONAL",
        ),
        sa.Column("frequency_per_day", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("total_tweets", sa.Integer(), nullable=False),
        sa.Column("tweets_posted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tweets_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posting_start_hour", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("posting_end_hour", sa.Integer(), nullable=False, server_default="21"),
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="UTC"),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED",
                name="campaignstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("web_search_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("search_keywords", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("custom_instructions", sa.Text(), nullable=True),
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
    op.create_index(
        op.f("ix_auto_campaigns_id"), "auto_campaigns", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_auto_campaigns_user_id"), "auto_campaigns", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_auto_campaigns_status"), "auto_campaigns", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_auto_campaigns_deleted_at"), "auto_campaigns", ["deleted_at"], unique=False
    )

    # Add campaign columns to scheduled_tweets
    op.add_column(
        "scheduled_tweets",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "scheduled_tweets",
        sa.Column("is_campaign_tweet", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "scheduled_tweets",
        sa.Column("content_generated", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_scheduled_tweets_campaign_id",
        "scheduled_tweets",
        "auto_campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Create index on campaign_id
    op.create_index(
        op.f("ix_scheduled_tweets_campaign_id"),
        "scheduled_tweets",
        ["campaign_id"],
        unique=False,
    )


def downgrade() -> None:
    # Remove campaign columns from scheduled_tweets
    op.drop_index(op.f("ix_scheduled_tweets_campaign_id"), table_name="scheduled_tweets")
    op.drop_constraint("fk_scheduled_tweets_campaign_id", "scheduled_tweets", type_="foreignkey")
    op.drop_column("scheduled_tweets", "content_generated")
    op.drop_column("scheduled_tweets", "is_campaign_tweet")
    op.drop_column("scheduled_tweets", "campaign_id")

    # Drop auto_campaigns table
    op.drop_index(op.f("ix_auto_campaigns_deleted_at"), table_name="auto_campaigns")
    op.drop_index(op.f("ix_auto_campaigns_status"), table_name="auto_campaigns")
    op.drop_index(op.f("ix_auto_campaigns_user_id"), table_name="auto_campaigns")
    op.drop_index(op.f("ix_auto_campaigns_id"), table_name="auto_campaigns")
    op.drop_table("auto_campaigns")

    # Drop campaignstatus enum
    op.execute("DROP TYPE IF EXISTS campaignstatus")

    # Note: Removing enum values is not easily supported in PostgreSQL
    # The enum values for apikeytype, tweetstatus, and auditaction will remain
