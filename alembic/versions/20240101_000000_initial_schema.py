"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="UTC"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "role",
            sa.Enum("USER", "ADMIN", name="userrole"),
            nullable=False,
            server_default="USER",
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("default_prompt_template", sa.Text(), nullable=True),
        sa.Column(
            "default_tone", sa.String(length=50), nullable=False, server_default="professional"
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_deleted_at"), "users", ["deleted_at"], unique=False)

    # Create oauth_accounts table
    op.create_table(
        "oauth_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider",
            sa.Enum("TWITTER", name="oauthprovider"),
            nullable=False,
        ),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("provider_username", sa.String(length=255), nullable=True),
        sa.Column("provider_display_name", sa.String(length=255), nullable=True),
        sa.Column("provider_profile_image", sa.Text(), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_scope", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
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
    op.create_index(op.f("ix_oauth_accounts_id"), "oauth_accounts", ["id"], unique=False)
    op.create_index(
        op.f("ix_oauth_accounts_user_id"), "oauth_accounts", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_oauth_accounts_provider"), "oauth_accounts", ["provider"], unique=False
    )
    op.create_index(
        op.f("ix_oauth_accounts_provider_user_id"),
        "oauth_accounts",
        ["provider_user_id"],
        unique=False,
    )

    # Create encrypted_api_keys table
    op.create_table(
        "encrypted_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "key_type",
            sa.Enum("DEEPSEEK", name="apikeytype"),
            nullable=False,
        ),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=20), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        op.f("ix_encrypted_api_keys_id"), "encrypted_api_keys", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_encrypted_api_keys_user_id"),
        "encrypted_api_keys",
        ["user_id"],
        unique=False,
    )

    # Create tweet_drafts table
    op.create_table(
        "tweet_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_thread", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("thread_contents", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("generated_by_ai", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("prompt_used", sa.Text(), nullable=True),
        sa.Column(
            "tone_used",
            sa.Enum(
                "PROFESSIONAL",
                "CASUAL",
                "VIRAL",
                "THOUGHT_LEADERSHIP",
                name="tweettone",
            ),
            nullable=True,
        ),
        sa.Column("character_count", sa.Integer(), nullable=False, server_default="0"),
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
    op.create_index(op.f("ix_tweet_drafts_id"), "tweet_drafts", ["id"], unique=False)
    op.create_index(
        op.f("ix_tweet_drafts_user_id"), "tweet_drafts", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_tweet_drafts_deleted_at"), "tweet_drafts", ["deleted_at"], unique=False
    )

    # Create scheduled_tweets table
    op.create_table(
        "scheduled_tweets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_thread", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("thread_contents", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="UTC"),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "PENDING",
                "POSTING",
                "POSTED",
                "FAILED",
                "CANCELLED",
                "RETRYING",
                name="tweetstatus",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("twitter_tweet_id", sa.String(length=255), nullable=True),
        sa.Column(
            "twitter_thread_ids", postgresql.ARRAY(sa.String(length=255)), nullable=True
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["draft_id"], ["tweet_drafts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_scheduled_tweets_id"), "scheduled_tweets", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_scheduled_tweets_user_id"), "scheduled_tweets", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_scheduled_tweets_draft_id"),
        "scheduled_tweets",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_tweets_scheduled_for"),
        "scheduled_tweets",
        ["scheduled_for"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_tweets_status"), "scheduled_tweets", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_scheduled_tweets_twitter_tweet_id"),
        "scheduled_tweets",
        ["twitter_tweet_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_tweets_deleted_at"),
        "scheduled_tweets",
        ["deleted_at"],
        unique=False,
    )

    # Create tweet_execution_logs table
    op.create_table(
        "tweet_execution_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_tweet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "PENDING",
                "POSTING",
                "POSTED",
                "FAILED",
                "CANCELLED",
                "RETRYING",
                name="tweetstatus",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("twitter_response", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["scheduled_tweet_id"], ["scheduled_tweets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tweet_execution_logs_id"),
        "tweet_execution_logs",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tweet_execution_logs_scheduled_tweet_id"),
        "tweet_execution_logs",
        ["scheduled_tweet_id"],
        unique=False,
    )

    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "USER_REGISTERED",
                "USER_LOGIN",
                "USER_LOGOUT",
                "USER_LOGIN_FAILED",
                "PASSWORD_CHANGED",
                "TWITTER_CONNECTED",
                "TWITTER_DISCONNECTED",
                "TWITTER_TOKEN_REFRESHED",
                "API_KEY_CREATED",
                "API_KEY_ROTATED",
                "API_KEY_DELETED",
                "TWEET_GENERATED",
                "TWEET_SCHEDULED",
                "TWEET_POSTED",
                "TWEET_FAILED",
                "TWEET_CANCELLED",
                "TWEET_EDITED",
                "TWEET_DELETED",
                "SETTINGS_UPDATED",
                "TIMEZONE_CHANGED",
                "USER_ROLE_CHANGED",
                "USER_DEACTIVATED",
                "USER_ACTIVATED",
                name="auditaction",
            ),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(length=100), nullable=True),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_id"), "audit_logs", ["id"], unique=False)
    op.create_index(
        op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_resource_type"),
        "audit_logs",
        ["resource_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_resource_id"), "audit_logs", ["resource_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("tweet_execution_logs")
    op.drop_table("scheduled_tweets")
    op.drop_table("tweet_drafts")
    op.drop_table("encrypted_api_keys")
    op.drop_table("oauth_accounts")
    op.drop_table("users")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS auditaction")
    op.execute("DROP TYPE IF EXISTS tweetstatus")
    op.execute("DROP TYPE IF EXISTS tweettone")
    op.execute("DROP TYPE IF EXISTS apikeytype")
    op.execute("DROP TYPE IF EXISTS oauthprovider")
    op.execute("DROP TYPE IF EXISTS userrole")
