"""Add growth strategy audit action enum values.

Revision ID: 004
Revises: 003
Create Date: 2024-12-19
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new audit action enum values for growth strategies
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'growth_strategy_created'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'growth_strategy_paused'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'growth_strategy_resumed'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'growth_strategy_cancelled'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'growth_strategy_completed'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # Would need to recreate the type, which is complex
    pass
