"""Add system logs and task executions tables.

Revision ID: 005
Revises: 004
Create Date: 2024-12-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create LogLevel enum (check if exists first)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE loglevel AS ENUM ('debug', 'info', 'warning', 'error', 'critical');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # Create LogCategory enum (check if exists first)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE logcategory AS ENUM ('system', 'celery', 'growth', 'campaign', 'tweet', 'auth', 'api', 'database', 'twitter', 'deepseek');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # Create enums using postgresql dialect
    loglevel = postgresql.ENUM('debug', 'info', 'warning', 'error', 'critical', name='loglevel', create_type=False)
    logcategory = postgresql.ENUM('system', 'celery', 'growth', 'campaign', 'tweet', 'auth', 'api', 'database', 'twitter', 'deepseek', name='logcategory', create_type=False)

    # Create system_logs table
    op.create_table(
        'system_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('level', loglevel, nullable=False),
        sa.Column('category', logcategory, nullable=False),
        sa.Column('logger_name', sa.String(255), nullable=False),
        sa.Column('task_name', sa.String(255), nullable=True),
        sa.Column('task_id', sa.String(255), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('exception_type', sa.String(255), nullable=True),
        sa.Column('exception_message', sa.Text(), nullable=True),
        sa.Column('traceback', sa.Text(), nullable=True),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('strategy_id', sa.UUID(), nullable=True),
        sa.Column('campaign_id', sa.UUID(), nullable=True),
        sa.Column('tweet_id', sa.UUID(), nullable=True),
        sa.Column('request_id', sa.String(255), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for system_logs
    op.create_index('ix_system_logs_timestamp', 'system_logs', ['timestamp'])
    op.create_index('ix_system_logs_level', 'system_logs', ['level'])
    op.create_index('ix_system_logs_category', 'system_logs', ['category'])
    op.create_index('ix_system_logs_logger_name', 'system_logs', ['logger_name'])
    op.create_index('ix_system_logs_task_name', 'system_logs', ['task_name'])
    op.create_index('ix_system_logs_user_id', 'system_logs', ['user_id'])
    op.create_index('ix_system_logs_strategy_id', 'system_logs', ['strategy_id'])
    op.create_index('ix_system_logs_campaign_id', 'system_logs', ['campaign_id'])
    op.create_index('ix_system_logs_timestamp_level', 'system_logs', ['timestamp', 'level'])
    op.create_index('ix_system_logs_category_timestamp', 'system_logs', ['category', 'timestamp'])

    # Create task_executions table
    op.create_table(
        'task_executions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('task_id', sa.String(255), nullable=False),
        sa.Column('task_name', sa.String(255), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('args', sa.JSON(), nullable=True),
        sa.Column('kwargs', sa.JSON(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('worker_hostname', sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id')
    )

    # Create indexes for task_executions
    op.create_index('ix_task_executions_task_id', 'task_executions', ['task_id'])
    op.create_index('ix_task_executions_task_name', 'task_executions', ['task_name'])
    op.create_index('ix_task_executions_status', 'task_executions', ['status'])
    op.create_index('ix_task_executions_name_started', 'task_executions', ['task_name', 'started_at'])


def downgrade() -> None:
    op.drop_table('task_executions')
    op.drop_table('system_logs')
    op.execute('DROP TYPE logcategory')
    op.execute('DROP TYPE loglevel')
